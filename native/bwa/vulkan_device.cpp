// The Vulkan device for wgse-bwa's GPU scheduling (backend.cpp): seed
// extension only, with extend.comp (a port of extend.metal) compiled to SPIR-V
// at build time. The Vulkan loader is opened at run time, so the binary runs
// without one and the backend then reports itself unavailable.
//
// Every batch is split into submissions of at most submit_jobs jobs, so that
// each GPU job stays short (about 15 ms on a 6-CU integrated Radeon): a GPU
// that also drives the display must not be held for long, and drivers reset
// GPUs whose jobs exceed their timeout. Each batch's wait has a deadline (the
// watchdog's floor, or 20 times the slowest batch); a batch past it, or a lost
// device, fails, and the CPU finishes its pairs and the rest of the run.

#include "gpu_device.h"
#include "extend_spv.h"

#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>

#include <dlfcn.h>

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <mutex>
#include <stdexcept>
#include <thread>

namespace wgse::gpu {
namespace {

#define WGSE_VK_FUNCTIONS(X) \
    X(vkDestroyInstance) X(vkEnumeratePhysicalDevices) X(vkGetPhysicalDeviceProperties) \
    X(vkGetPhysicalDeviceProperties2) X(vkGetPhysicalDeviceQueueFamilyProperties) \
    X(vkGetPhysicalDeviceMemoryProperties) X(vkGetPhysicalDeviceFeatures2) \
    X(vkCreateDevice) X(vkDestroyDevice) X(vkGetDeviceQueue) X(vkDeviceWaitIdle) \
    X(vkCreateBuffer) X(vkDestroyBuffer) X(vkGetBufferMemoryRequirements) \
    X(vkAllocateMemory) X(vkFreeMemory) X(vkBindBufferMemory) X(vkMapMemory) \
    X(vkCreateShaderModule) X(vkDestroyShaderModule) X(vkCreateDescriptorSetLayout) \
    X(vkDestroyDescriptorSetLayout) X(vkCreatePipelineLayout) X(vkDestroyPipelineLayout) \
    X(vkCreateComputePipelines) X(vkDestroyPipeline) X(vkCreateDescriptorPool) \
    X(vkDestroyDescriptorPool) X(vkAllocateDescriptorSets) X(vkUpdateDescriptorSets) \
    X(vkCreateCommandPool) X(vkDestroyCommandPool) X(vkResetCommandPool) \
    X(vkAllocateCommandBuffers) X(vkBeginCommandBuffer) X(vkEndCommandBuffer) \
    X(vkCmdBindPipeline) X(vkCmdBindDescriptorSets) X(vkCmdPushConstants) X(vkCmdDispatch) \
    X(vkCmdPipelineBarrier) X(vkCmdCopyBuffer) X(vkQueueSubmit) X(vkCreateFence) \
    X(vkDestroyFence) X(vkWaitForFences) X(vkResetFences)

// The loader and its entry points; null members if it or a function is missing.
struct Vk {
    void *library = nullptr;
    PFN_vkGetInstanceProcAddr vkGetInstanceProcAddr = nullptr;
    PFN_vkCreateInstance vkCreateInstance = nullptr;
    PFN_vkEnumerateInstanceVersion vkEnumerateInstanceVersion = nullptr;
#define WGSE_VK_MEMBER(name) PFN_##name name = nullptr;
    WGSE_VK_FUNCTIONS(WGSE_VK_MEMBER)
#undef WGSE_VK_MEMBER
};

Vk &vk() {
    static Vk table;
    return table;
}

// Opens the loader once; false (with a reason) if there is none.
bool open_loader(std::string &reason) {
    Vk &v = vk();
    if (v.vkGetInstanceProcAddr) return true;
    for (const char *name : {"libvulkan.so.1", "libvulkan.so"}) {
        v.library = dlopen(name, RTLD_NOW | RTLD_LOCAL);
        if (v.library) break;
    }
    if (!v.library) {
        reason = "no Vulkan loader (libvulkan.so.1)";
        return false;
    }
    v.vkGetInstanceProcAddr = reinterpret_cast<PFN_vkGetInstanceProcAddr>(
        dlsym(v.library, "vkGetInstanceProcAddr"));
    if (!v.vkGetInstanceProcAddr) {
        reason = "the Vulkan loader has no vkGetInstanceProcAddr";
        return false;
    }
    v.vkCreateInstance = reinterpret_cast<PFN_vkCreateInstance>(
        v.vkGetInstanceProcAddr(nullptr, "vkCreateInstance"));
    v.vkEnumerateInstanceVersion = reinterpret_cast<PFN_vkEnumerateInstanceVersion>(
        v.vkGetInstanceProcAddr(nullptr, "vkEnumerateInstanceVersion"));
    return v.vkCreateInstance != nullptr;
}

bool load_instance_functions(VkInstance instance) {
    Vk &v = vk();
    bool ok = true;
#define WGSE_VK_LOAD(name) \
    v.name = reinterpret_cast<PFN_##name>(v.vkGetInstanceProcAddr(instance, #name)); \
    ok = ok && v.name;
    WGSE_VK_FUNCTIONS(WGSE_VK_LOAD)
#undef WGSE_VK_LOAD
    return ok;
}

// A Vulkan structure of type `type`, zeroed.
template <class T>
T make(VkStructureType type) {
    T value{};
    value.sType = type;
    return value;
}

void check(VkResult result, const char *what) {
    if (result != VK_SUCCESS)
        throw std::runtime_error(std::string("Vulkan ") + what + " failed (VkResult "
            + std::to_string(int(result)) + ")");
}

// Jobs per submission; see the comment at the top.
constexpr size_t submit_jobs = 8192;
constexpr uint32_t workgroup = 64;
constexpr uint32_t required_operations = VK_SUBGROUP_FEATURE_BASIC_BIT
    | VK_SUBGROUP_FEATURE_VOTE_BIT | VK_SUBGROUP_FEATURE_SHUFFLE_BIT
    | VK_SUBGROUP_FEATURE_SHUFFLE_RELATIVE_BIT;

// extend.comp's push constants: ExtendParameters, the side and the class's
// first slot in the order buffer.
struct Push {
    ExtendParameters p;
    uint32_t right, first;
};
static_assert(sizeof(Push) == 60);

// A physical device that can run extend.comp, and how.
struct Candidate {
    VkPhysicalDevice device = VK_NULL_HANDLE;
    VkPhysicalDeviceProperties properties{};
    uint32_t queue_family = 0;
    // 0, or the subgroup size to require (VK_EXT_subgroup_size_control) when
    // the default is not a multiple of 16.
    uint32_t required_subgroup = 0;
    int rank = 0;
};

// Why a device cannot run the kernel, or "" if it can.
std::string assess(VkPhysicalDevice device, Candidate &c) {
    Vk &v = vk();
    c.device = device;
    v.vkGetPhysicalDeviceProperties(device, &c.properties);
    const auto &p = c.properties;
    if (VK_API_VERSION_MINOR(p.apiVersion) < 1 && VK_API_VERSION_MAJOR(p.apiVersion) == 1)
        return "Vulkan 1.1 or newer is needed";
    if (p.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU) return "a CPU (software) device";
    auto size_control = make<VkPhysicalDeviceSubgroupSizeControlProperties>(VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_SIZE_CONTROL_PROPERTIES);
    auto subgroup = make<VkPhysicalDeviceSubgroupProperties>(VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_PROPERTIES);
    const bool v13 = VK_API_VERSION_MINOR(p.apiVersion) >= 3;
    subgroup.pNext = v13 ? &size_control : nullptr;
    auto properties2 = make<VkPhysicalDeviceProperties2>(VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2);
    properties2.pNext = &subgroup;
    v.vkGetPhysicalDeviceProperties2(device, &properties2);
    if (!(subgroup.supportedStages & VK_SHADER_STAGE_COMPUTE_BIT)
        || (subgroup.supportedOperations & required_operations) != required_operations)
        return "no subgroup vote and shuffle operations in compute shaders";
    c.required_subgroup = 0;
    if (subgroup.subgroupSize % 16 || subgroup.subgroupSize > workgroup) {
        auto features13 = make<VkPhysicalDeviceVulkan13Features>(VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES);
        auto features = make<VkPhysicalDeviceFeatures2>(VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2);
        features.pNext = &features13;
        if (v13) v.vkGetPhysicalDeviceFeatures2(device, &features);
        const bool control = v13 && features13.subgroupSizeControl
            && (size_control.requiredSubgroupSizeStages & VK_SHADER_STAGE_COMPUTE_BIT);
        for (uint32_t size : {32u, 16u, 64u})
            if (control && size >= size_control.minSubgroupSize
                && size <= size_control.maxSubgroupSize && size <= workgroup) {
                c.required_subgroup = size;
                break;
            }
        if (!c.required_subgroup)
            return "its subgroup size (" + std::to_string(subgroup.subgroupSize)
                + ") is not a multiple of 16";
    }
    uint32_t families = 0;
    v.vkGetPhysicalDeviceQueueFamilyProperties(device, &families, nullptr);
    std::vector<VkQueueFamilyProperties> family(families);
    v.vkGetPhysicalDeviceQueueFamilyProperties(device, &families, family.data());
    bool found = false;
    // A compute-only family if there is one, else any with compute.
    for (int pass = 0; pass < 2 && !found; ++pass)
        for (uint32_t i = 0; i < families && !found; ++i) {
            const bool compute = family[i].queueFlags & VK_QUEUE_COMPUTE_BIT;
            const bool graphics = family[i].queueFlags & VK_QUEUE_GRAPHICS_BIT;
            if (compute && (pass || !graphics)) {
                c.queue_family = i;
                found = true;
            }
        }
    if (!found) return "no compute queue";
    c.rank = p.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU ? 3
        : p.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU ? 2 : 1;
    return "";
}

struct Buffer {
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    void *mapped = nullptr;
    VkDeviceSize size = 0;
};

// Set when a batch misses its deadline or the device is lost: nothing is
// destroyed afterwards, since that would wait for the hung work.
std::atomic<bool> vulkan_hung{false};

class VulkanDevice;

struct VulkanExtendBuffers : ExtendBuffers {
    VulkanDevice *owner = nullptr;
    // Host-visible, cached: the CPU writes jobs and reads results in place.
    Buffer host_bases, host_jobs, host_results, host_order;
    // Device-local copies on a discrete GPU; unused on an integrated one.
    Buffer device_bases, device_jobs, device_results, device_order;
    bool staged = false;
    VkDescriptorSet descriptors = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
    std::vector<VkCommandBuffer> commands;
    VkFence fence = VK_NULL_HANDLE;
    ~VulkanExtendBuffers() override;
};

class VulkanDevice final : public Device {
public:
    VulkanDevice(VkInstance instance, const Candidate &c, const DeviceOptions &options)
        : instance_(instance), physical_(c.device), properties_(c.properties),
          family_(c.queue_family), deadline_floor_(options.watchdog_seconds) {
        Vk &v = vk();
        v.vkGetPhysicalDeviceMemoryProperties(physical_, &memory_);
        integrated_ = properties_.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU;
        snprintf(name_, sizeof name_, "Vulkan");
        const float priority = 1;
        auto queue = make<VkDeviceQueueCreateInfo>(VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO);
        queue.queueFamilyIndex = family_;
        queue.queueCount = 1;
        queue.pQueuePriorities = &priority;
        auto features13 = make<VkPhysicalDeviceVulkan13Features>(VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES);
        features13.subgroupSizeControl = c.required_subgroup ? VK_TRUE : VK_FALSE;
        auto create = make<VkDeviceCreateInfo>(VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO);
        create.pNext = c.required_subgroup ? &features13 : nullptr;
        create.queueCreateInfoCount = 1;
        create.pQueueCreateInfos = &queue;
        check(v.vkCreateDevice(physical_, &create, nullptr, &device_), "device creation");
        v.vkGetDeviceQueue(device_, family_, 0, &queue_);
        build_pipelines(c.required_subgroup);
        completer_ = std::thread(&VulkanDevice::complete_loop, this);
    }

    ~VulkanDevice() override {
        {
            std::lock_guard<std::mutex> lock(completion_mutex_);
            stopping_ = true;
        }
        completion_changed_.notify_all();
        if (completer_.joinable()) completer_.join();
        if (vulkan_hung) return;
        Vk &v = vk();
        v.vkDeviceWaitIdle(device_);
        for (VkPipeline pipeline : pipelines_)
            if (pipeline) v.vkDestroyPipeline(device_, pipeline, nullptr);
        if (shader_) v.vkDestroyShaderModule(device_, shader_, nullptr);
        if (layout_) v.vkDestroyPipelineLayout(device_, layout_, nullptr);
        if (set_layout_) v.vkDestroyDescriptorSetLayout(device_, set_layout_, nullptr);
        if (descriptor_pool_) v.vkDestroyDescriptorPool(device_, descriptor_pool_, nullptr);
        v.vkDestroyDevice(device_, nullptr);
        v.vkDestroyInstance(instance_, nullptr);
    }

    const char *name() const override { return name_; }

    bool seeds() const override { return false; }
    bool rescues() const override { return false; }
    size_t seed_capacity() const override { return 0; }
    void attach(const bwt_t &, const mem_opt_t &) override {}
    void seed(const Reads &, std::vector<Intervals> &, std::vector<long> &) override {
        throw std::logic_error("the Vulkan device does not seed");
    }
    bool attach_sa(const bwt_t &) override { return false; }
    void locate(const std::vector<uint64_t> &, std::vector<uint64_t> &) override {
        throw std::logic_error("the Vulkan device does not locate");
    }
    bool densify(unsigned, size_t, const uint32_t **, const uint32_t **) override { return false; }
    void rescue(const RescueParameters &, const std::vector<uint8_t> &,
        const std::vector<RescueJob> &, size_t, std::vector<RescueResult> &) override {
        throw std::logic_error("the Vulkan device does not rescue");
    }
    double rescue_kernel_seconds() const override { return 0; }

    std::unique_ptr<ExtendBuffers> extend_buffers(size_t bases, size_t jobs) override {
        auto b = std::make_unique<VulkanExtendBuffers>();
        b->owner = this;
        // extend.comp reads bases as 32-bit words.
        const size_t base_bytes = (std::max<size_t>(bases, 1) + 3) / 4 * 4;
        const VkMemoryPropertyFlags host = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT
            | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT;
        const VkBufferUsageFlags usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT
            | VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
        b->host_bases = allocate(base_bytes, usage, host);
        b->host_jobs = allocate(jobs * sizeof(ExtendJob), usage, host);
        b->host_results = allocate(jobs * sizeof(ExtendResult), usage, host);
        b->host_order = allocate(2 * jobs * sizeof(uint32_t), usage, host);
        // A discrete GPU would read the host's memory across PCIe for every
        // row; it gets device-local copies instead.
        b->staged = !integrated_;
        if (b->staged) {
            const VkMemoryPropertyFlags local = VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
            b->device_bases = allocate(base_bytes, usage, local);
            b->device_jobs = allocate(jobs * sizeof(ExtendJob), usage, local);
            b->device_results = allocate(jobs * sizeof(ExtendResult), usage, local);
            b->device_order = allocate(2 * jobs * sizeof(uint32_t), usage, local);
        }
        b->bases = static_cast<uint8_t *>(b->host_bases.mapped);
        b->jobs = static_cast<ExtendJob *>(b->host_jobs.mapped);
        b->results = static_cast<ExtendResult *>(b->host_results.mapped);
        b->base_capacity = bases;
        b->job_capacity = jobs;
        Vk &v = vk();
        {
            std::lock_guard<std::mutex> lock(descriptor_mutex_);
            auto allocate_info = make<VkDescriptorSetAllocateInfo>(VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO);
            allocate_info.descriptorPool = descriptor_pool_;
            allocate_info.descriptorSetCount = 1;
            allocate_info.pSetLayouts = &set_layout_;
            check(v.vkAllocateDescriptorSets(device_, &allocate_info, &b->descriptors),
                "descriptor allocation");
        }
        const Buffer *bound[4] = {
            b->staged ? &b->device_bases : &b->host_bases,
            b->staged ? &b->device_jobs : &b->host_jobs,
            b->staged ? &b->device_results : &b->host_results,
            b->staged ? &b->device_order : &b->host_order};
        VkDescriptorBufferInfo info[4];
        VkWriteDescriptorSet writes[4];
        for (uint32_t i = 0; i < 4; ++i) {
            info[i] = {bound[i]->buffer, 0, VK_WHOLE_SIZE};
            writes[i] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, nullptr, b->descriptors, i, 0, 1,
                VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, nullptr, &info[i], nullptr};
        }
        v.vkUpdateDescriptorSets(device_, 4, writes, 0, nullptr);
        auto pool = make<VkCommandPoolCreateInfo>(VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO);
        pool.queueFamilyIndex = family_;
        check(v.vkCreateCommandPool(device_, &pool, nullptr, &b->pool), "command pool");
        b->commands.resize((jobs + submit_jobs - 1) / submit_jobs + 2);
        auto commands = make<VkCommandBufferAllocateInfo>(VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO);
        commands.commandPool = b->pool;
        commands.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
        commands.commandBufferCount = uint32_t(b->commands.size());
        check(v.vkAllocateCommandBuffers(device_, &commands, b->commands.data()), "command buffers");
        auto fence = make<VkFenceCreateInfo>(VK_STRUCTURE_TYPE_FENCE_CREATE_INFO);
        check(v.vkCreateFence(device_, &fence, nullptr, &b->fence), "fence");
        return b;
    }

    void extend(const ExtendParameters &p, ExtendBuffers &base, size_t count, int,
        ExtendDone done) override {
        if (vulkan_hung) throw std::runtime_error("Vulkan: the GPU stopped responding earlier");
        auto &b = static_cast<VulkanExtendBuffers &>(base);
        Vk &v = vk();
        auto *order = static_cast<uint32_t *>(b.host_order.mapped);
        const size_t capacity = b.job_capacity;
        check(v.vkResetCommandPool(device_, b.pool, 0), "command pool reset");
        size_t used = 0;
        auto begin = make<VkCommandBufferBeginInfo>(VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO);
        begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
        if (b.staged) {
            // The batch's bases end at the last job's last base.
            size_t bytes = 0;
            for (size_t i = 0; i < count; ++i) {
                const ExtendJob &j = b.jobs[i];
                bytes = std::max<size_t>(bytes, std::max(size_t(j.left_target) + j.left_tlen,
                    size_t(j.right_target) + j.right_tlen));
            }
            VkCommandBuffer upload = b.commands[used++];
            check(v.vkBeginCommandBuffer(upload, &begin), "command recording");
            copy(upload, b.host_bases, b.device_bases, (std::max<size_t>(bytes, 1) + 3) / 4 * 4);
            copy(upload, b.host_jobs, b.device_jobs, count * sizeof(ExtendJob));
            barrier(upload, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_ACCESS_TRANSFER_WRITE_BIT,
                VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT);
            check(v.vkEndCommandBuffer(upload), "command recording");
        }
        std::vector<VkCommandBuffer> chunks;
        for (size_t low = 0; low < count; low += submit_jobs) {
            const size_t high = std::min(count, low + submit_jobs);
            size_t sides[2];
            for (int side = 0; side < 2; ++side) {
                uint32_t *slots = order + side * capacity + low;
                sides[side] = order_side(b.jobs + low, high - low, side == 1, slots);
                for (size_t i = 0; i < sides[side]; ++i) slots[i] += uint32_t(low);
            }
            VkCommandBuffer cb = b.commands[used++];
            check(v.vkBeginCommandBuffer(cb, &begin), "command recording");
            if (b.staged) {
                for (int side = 0; side < 2; ++side)
                    if (sides[side])
                        copy(cb, b.host_order, b.device_order, sides[side] * sizeof(uint32_t),
                            (side * capacity + low) * sizeof(uint32_t));
                barrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_ACCESS_TRANSFER_WRITE_BIT,
                    VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_READ_BIT);
            }
            v.vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, layout_, 0, 1,
                &b.descriptors, 0, nullptr);
            // The right side reads the left side's results, as on Metal.
            for (int side = 0; side < 2; ++side) {
                const size_t start = side * capacity + low;
                const uint32_t *slots = order + start;
                for_each_class(b.jobs, slots, sides[side], side == 1,
                    [&](size_t k, size_t first, size_t last) {
                        Push push{p, uint32_t(side), uint32_t(start + first)};
                        push.p.count = uint32_t(last - first);
                        v.vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pipelines_[k]);
                        v.vkCmdPushConstants(cb, layout_, VK_SHADER_STAGE_COMPUTE_BIT, 0,
                            sizeof(push), &push);
                        const size_t threads = (last - first) * extend_lanes;
                        v.vkCmdDispatch(cb, uint32_t((threads + workgroup - 1) / workgroup), 1, 1);
                    });
                barrier(cb, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_WRITE_BIT,
                    VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_TRANSFER_BIT
                        | VK_PIPELINE_STAGE_HOST_BIT,
                    VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_TRANSFER_READ_BIT
                        | VK_ACCESS_HOST_READ_BIT);
            }
            check(v.vkEndCommandBuffer(cb), "command recording");
        }
        if (b.staged) {
            VkCommandBuffer download = b.commands[used++];
            check(v.vkBeginCommandBuffer(download, &begin), "command recording");
            copy(download, b.device_results, b.host_results, count * sizeof(ExtendResult));
            barrier(download, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_ACCESS_TRANSFER_WRITE_BIT,
                VK_PIPELINE_STAGE_HOST_BIT, VK_ACCESS_HOST_READ_BIT);
            check(v.vkEndCommandBuffer(download), "command recording");
        }
        {
            // One GPU job per command buffer; the last signals the batch's fence.
            std::lock_guard<std::mutex> lock(queue_mutex_);
            for (size_t i = 0; i < used; ++i) {
                auto submit = make<VkSubmitInfo>(VK_STRUCTURE_TYPE_SUBMIT_INFO);
                submit.commandBufferCount = 1;
                submit.pCommandBuffers = &b.commands[i];
                check(v.vkQueueSubmit(queue_, 1, &submit, i + 1 == used ? b.fence : VK_NULL_HANDLE),
                    "submission");
            }
        }
        std::lock_guard<std::mutex> lock(completion_mutex_);
        pending_.push_back({&b, Clock::now(), std::move(done)});
        completion_changed_.notify_one();
    }

    void free(Buffer &buffer) {
        Vk &v = vk();
        if (buffer.buffer) v.vkDestroyBuffer(device_, buffer.buffer, nullptr);
        if (buffer.memory) v.vkFreeMemory(device_, buffer.memory, nullptr);
        buffer = {};
    }

    void free(VulkanExtendBuffers &b) {
        if (vulkan_hung) return;
        Vk &v = vk();
        if (b.fence) v.vkWaitForFences(device_, 1, &b.fence, VK_TRUE, UINT64_MAX);
        for (Buffer *buffer : {&b.host_bases, &b.host_jobs, &b.host_results, &b.host_order,
                 &b.device_bases, &b.device_jobs, &b.device_results, &b.device_order})
            free(*buffer);
        if (b.pool) v.vkDestroyCommandPool(device_, b.pool, nullptr);
        if (b.fence) v.vkDestroyFence(device_, b.fence, nullptr);
    }

private:
    struct Pending {
        VulkanExtendBuffers *buffers;
        Clock::time_point submitted;
        ExtendDone done;
    };

    VkInstance instance_;
    VkPhysicalDevice physical_;
    VkPhysicalDeviceProperties properties_;
    VkPhysicalDeviceMemoryProperties memory_{};
    uint32_t family_;
    bool integrated_ = false;
    char name_[16];
    VkDevice device_ = VK_NULL_HANDLE;
    VkQueue queue_ = VK_NULL_HANDLE;
    VkShaderModule shader_ = VK_NULL_HANDLE;
    VkDescriptorSetLayout set_layout_ = VK_NULL_HANDLE;
    VkPipelineLayout layout_ = VK_NULL_HANDLE;
    VkDescriptorPool descriptor_pool_ = VK_NULL_HANDLE;
    VkPipeline pipelines_[extend_classes] = {};
    std::mutex queue_mutex_, descriptor_mutex_;
    double deadline_floor_, slowest_ = 0;
    std::mutex completion_mutex_;
    std::condition_variable completion_changed_;
    std::deque<Pending> pending_;
    bool stopping_ = false;
    std::thread completer_;

    Buffer allocate(size_t bytes, VkBufferUsageFlags usage, VkMemoryPropertyFlags wanted) {
        Vk &v = vk();
        Buffer b;
        b.size = std::max<size_t>(bytes, 16);
        auto create = make<VkBufferCreateInfo>(VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO);
        create.size = b.size;
        create.usage = usage;
        check(v.vkCreateBuffer(device_, &create, nullptr, &b.buffer), "buffer creation");
        VkMemoryRequirements requirements;
        v.vkGetBufferMemoryRequirements(device_, b.buffer, &requirements);
        // The wanted properties, or failing that without HOST_CACHED (some
        // drivers have no cached coherent type).
        uint32_t type = UINT32_MAX;
        for (VkMemoryPropertyFlags flags : {wanted,
                 VkMemoryPropertyFlags(wanted & ~VK_MEMORY_PROPERTY_HOST_CACHED_BIT)})
            for (uint32_t i = 0; i < memory_.memoryTypeCount && type == UINT32_MAX; ++i)
                if ((requirements.memoryTypeBits >> i & 1)
                    && (memory_.memoryTypes[i].propertyFlags & flags) == flags)
                    type = i;
        if (type == UINT32_MAX) {
            v.vkDestroyBuffer(device_, b.buffer, nullptr);
            throw std::runtime_error("Vulkan: no suitable memory type");
        }
        auto allocate_info = make<VkMemoryAllocateInfo>(VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO);
        allocate_info.allocationSize = requirements.size;
        allocate_info.memoryTypeIndex = type;
        const VkResult result = v.vkAllocateMemory(device_, &allocate_info, nullptr, &b.memory);
        if (result != VK_SUCCESS) {
            v.vkDestroyBuffer(device_, b.buffer, nullptr);
            check(result, "memory allocation");
        }
        check(v.vkBindBufferMemory(device_, b.buffer, b.memory, 0), "memory binding");
        if (wanted & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT)
            check(v.vkMapMemory(device_, b.memory, 0, VK_WHOLE_SIZE, 0, &b.mapped), "mapping");
        return b;
    }

    static void copy(VkCommandBuffer cb, const Buffer &from, const Buffer &to, size_t bytes,
        size_t offset = 0) {
        if (!bytes) return;
        VkBufferCopy region{offset, offset, bytes};
        vk().vkCmdCopyBuffer(cb, from.buffer, to.buffer, 1, &region);
    }

    static void barrier(VkCommandBuffer cb, VkPipelineStageFlags from, VkAccessFlags written,
        VkPipelineStageFlags to, VkAccessFlags read) {
        VkMemoryBarrier b{VK_STRUCTURE_TYPE_MEMORY_BARRIER, nullptr, written, read};
        vk().vkCmdPipelineBarrier(cb, from, to, 0, 1, &b, 0, nullptr, 0, nullptr);
    }

    void build_pipelines(uint32_t required_subgroup) {
        Vk &v = vk();
        auto module = make<VkShaderModuleCreateInfo>(VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO);
        module.codeSize = sizeof(extend_spv);
        module.pCode = extend_spv;
        check(v.vkCreateShaderModule(device_, &module, nullptr, &shader_), "shader module");
        VkDescriptorSetLayoutBinding bindings[4];
        for (uint32_t i = 0; i < 4; ++i)
            bindings[i] = {i, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT,
                nullptr};
        auto set = make<VkDescriptorSetLayoutCreateInfo>(VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO);
        set.bindingCount = 4;
        set.pBindings = bindings;
        check(v.vkCreateDescriptorSetLayout(device_, &set, nullptr, &set_layout_),
            "descriptor layout");
        VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(Push)};
        auto layout = make<VkPipelineLayoutCreateInfo>(VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO);
        layout.setLayoutCount = 1;
        layout.pSetLayouts = &set_layout_;
        layout.pushConstantRangeCount = 1;
        layout.pPushConstantRanges = &range;
        check(v.vkCreatePipelineLayout(device_, &layout, nullptr, &layout_), "pipeline layout");
        // One set per extension batch (backend.cpp keeps a small pool of them).
        constexpr uint32_t sets = 64;
        VkDescriptorPoolSize size{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 4 * sets};
        auto pool = make<VkDescriptorPoolCreateInfo>(VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO);
        pool.flags = VK_DESCRIPTOR_POOL_CREATE_FREE_DESCRIPTOR_SET_BIT;
        pool.maxSets = sets;
        pool.poolSizeCount = 1;
        pool.pPoolSizes = &size;
        check(v.vkCreateDescriptorPool(device_, &pool, nullptr, &descriptor_pool_),
            "descriptor pool");
        auto subgroup = make<VkPipelineShaderStageRequiredSubgroupSizeCreateInfo>(VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_REQUIRED_SUBGROUP_SIZE_CREATE_INFO);
        subgroup.requiredSubgroupSize = required_subgroup;
        for (size_t k = 0; k < extend_classes; ++k) {
            // extend_class_caps[k] = T * 16 * C, as extend.metal's instantiations.
            const int32_t cap = int32_t(extend_class_caps[k]);
            const int32_t tiles = cap > 128 ? cap / 64 : 1;
            const int32_t constants[3] = {tiles, cap / (16 * tiles), int32_t(workgroup)};
            const VkSpecializationMapEntry entries[3] = {{0, 0, 4}, {1, 4, 4}, {2, 8, 4}};
            const VkSpecializationInfo specialization{3, entries, sizeof constants, constants};
            auto create = make<VkComputePipelineCreateInfo>(VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO);
            create.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
                required_subgroup ? &subgroup : nullptr, 0, VK_SHADER_STAGE_COMPUTE_BIT, shader_,
                "main", &specialization};
            create.layout = layout_;
            check(v.vkCreateComputePipelines(device_, VK_NULL_HANDLE, 1, &create, nullptr,
                      &pipelines_[k]), "pipeline creation");
        }
    }

    // Waits for each batch in submission order, up to its deadline.
    void complete_loop() {
        std::unique_lock<std::mutex> lock(completion_mutex_);
        for (;;) {
            completion_changed_.wait(lock, [this] { return stopping_ || !pending_.empty(); });
            if (pending_.empty()) return;
            Pending next = std::move(pending_.front());
            pending_.pop_front();
            lock.unlock();
            Outcome outcome = Outcome::done;
            std::string error;
            Vk &v = vk();
            const double allowed = std::max(deadline_floor_, 20 * slowest_);
            const VkResult result = v.vkWaitForFences(device_, 1, &next.buffers->fence, VK_TRUE,
                uint64_t(allowed * 1e9));
            const double elapsed = seconds(next.submitted);
            if (result == VK_SUCCESS) {
                slowest_ = std::max(slowest_, elapsed);
                v.vkResetFences(device_, 1, &next.buffers->fence);
            } else {
                vulkan_hung = true;
                outcome = Outcome::failed;
                error = result == VK_TIMEOUT
                    ? "GPU watchdog: Vulkan seed extension did not finish within "
                        + std::to_string(int(allowed)) + " s"
                    : "Vulkan seed extension failed (VkResult " + std::to_string(int(result)) + ")";
            }
            next.done(outcome, elapsed, error);
            lock.lock();
        }
    }
};

VulkanExtendBuffers::~VulkanExtendBuffers() {
    if (owner) owner->free(*this);
}

// The instance and the best device that can run the kernel, or a reason.
bool find_device(VkInstance &instance, Candidate &best, std::string &reason) {
    if (!open_loader(reason)) return false;
    Vk &v = vk();
    uint32_t version = VK_API_VERSION_1_0;
    if (v.vkEnumerateInstanceVersion) v.vkEnumerateInstanceVersion(&version);
    if (version < VK_API_VERSION_1_1) {
        reason = "the Vulkan loader is older than 1.1";
        return false;
    }
    auto app = make<VkApplicationInfo>(VK_STRUCTURE_TYPE_APPLICATION_INFO);
    app.pApplicationName = "wgse-bwa";
    app.apiVersion = version >= VK_API_VERSION_1_3 ? VK_API_VERSION_1_3 : VK_API_VERSION_1_1;
    auto create = make<VkInstanceCreateInfo>(VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO);
    create.pApplicationInfo = &app;
    if (v.vkCreateInstance(&create, nullptr, &instance) != VK_SUCCESS) {
        reason = "no Vulkan driver (instance creation failed)";
        return false;
    }
    if (!load_instance_functions(instance)) {
        reason = "the Vulkan loader lacks required functions";
        return false;
    }
    uint32_t count = 0;
    v.vkEnumeratePhysicalDevices(instance, &count, nullptr);
    std::vector<VkPhysicalDevice> devices(count);
    v.vkEnumeratePhysicalDevices(instance, &count, devices.data());
    std::string why;
    for (VkPhysicalDevice device : devices) {
        Candidate c;
        const std::string problem = assess(device, c);
        if (!problem.empty()) {
            if (why.empty()) why = std::string(c.properties.deviceName) + ": " + problem;
            continue;
        }
        if (c.rank > best.rank) best = c;
    }
    if (!best.device) {
        reason = count == 0 ? "no Vulkan GPU" : "no usable Vulkan GPU (" + why + ")";
        v.vkDestroyInstance(instance, nullptr);
        return false;
    }
    return true;
}
} // namespace

std::unique_ptr<Device> make_vulkan_device(const DeviceOptions &options, std::string &reason) {
    VkInstance instance = VK_NULL_HANDLE;
    Candidate best;
    if (!find_device(instance, best, reason)) return nullptr;
    try {
        return std::make_unique<VulkanDevice>(instance, best, options);
    } catch (const std::exception &error) {
        reason = error.what();
        return nullptr;
    }
}

bool vulkan_hardware_supported() {
    VkInstance instance = VK_NULL_HANDLE;
    Candidate best;
    std::string reason;
    if (!find_device(instance, best, reason)) return false;
    vk().vkDestroyInstance(instance, nullptr);
    return true;
}
} // namespace wgse::gpu

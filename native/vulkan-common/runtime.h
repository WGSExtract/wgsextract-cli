// Runtime-loaded Vulkan entry points shared by the Vulkan compute backends.
// No loader or shader compiler is needed to run the CPU backend.
#pragma once

#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>

#include <dlfcn.h>

#include <mutex>
#include <stdexcept>
#include <string>

namespace wgse::vulkan {

#define WGSE_VK_FUNCTIONS(X) \
    X(vkDestroyInstance) X(vkEnumeratePhysicalDevices) X(vkEnumerateDeviceExtensionProperties) \
    X(vkGetPhysicalDeviceProperties) \
    X(vkGetPhysicalDeviceProperties2) X(vkGetPhysicalDeviceQueueFamilyProperties) \
    X(vkGetPhysicalDeviceMemoryProperties) X(vkGetPhysicalDeviceFeatures2) \
    X(vkCreateDevice) X(vkDestroyDevice) X(vkGetDeviceQueue) X(vkDeviceWaitIdle) \
    X(vkCreateBuffer) X(vkDestroyBuffer) X(vkGetBufferMemoryRequirements) \
    X(vkAllocateMemory) X(vkFreeMemory) X(vkBindBufferMemory) X(vkMapMemory) X(vkUnmapMemory) \
    X(vkCreateShaderModule) X(vkDestroyShaderModule) X(vkCreateDescriptorSetLayout) \
    X(vkDestroyDescriptorSetLayout) X(vkCreatePipelineLayout) X(vkDestroyPipelineLayout) \
    X(vkCreateComputePipelines) X(vkDestroyPipeline) X(vkCreateDescriptorPool) \
    X(vkDestroyDescriptorPool) X(vkAllocateDescriptorSets) X(vkFreeDescriptorSets) \
    X(vkUpdateDescriptorSets) X(vkCreateCommandPool) X(vkDestroyCommandPool) X(vkResetCommandPool) \
    X(vkAllocateCommandBuffers) X(vkBeginCommandBuffer) X(vkEndCommandBuffer) \
    X(vkCmdBindPipeline) X(vkCmdBindDescriptorSets) X(vkCmdPushConstants) X(vkCmdDispatch) \
    X(vkCmdPipelineBarrier) X(vkCmdCopyBuffer) X(vkQueueSubmit) X(vkCreateFence) \
    X(vkDestroyFence) X(vkWaitForFences) X(vkResetFences) \
    X(vkCreateQueryPool) X(vkDestroyQueryPool) X(vkCmdResetQueryPool) \
    X(vkCmdWriteTimestamp) X(vkGetQueryPoolResults)

struct Runtime {
    void *library = nullptr;
    PFN_vkGetInstanceProcAddr vkGetInstanceProcAddr = nullptr;
    PFN_vkCreateInstance vkCreateInstance = nullptr;
    PFN_vkEnumerateInstanceVersion vkEnumerateInstanceVersion = nullptr;
#define WGSE_VK_MEMBER(name) PFN_##name name = nullptr;
    WGSE_VK_FUNCTIONS(WGSE_VK_MEMBER)
#undef WGSE_VK_MEMBER
};

inline Runtime &vk() {
    static Runtime table;
    return table;
}

inline bool open_loader(std::string &reason) {
    static std::once_flag once;
    static std::string failure;
    std::call_once(once, [] {
        Runtime &v = vk();
        for (const char *name : {"libvulkan.so.1", "libvulkan.so"}) {
            v.library = dlopen(name, RTLD_NOW | RTLD_LOCAL);
            if (v.library) break;
        }
        if (!v.library) {
            failure = "no Vulkan loader (libvulkan.so.1)";
            return;
        }
        v.vkGetInstanceProcAddr = reinterpret_cast<PFN_vkGetInstanceProcAddr>(
            dlsym(v.library, "vkGetInstanceProcAddr"));
        if (!v.vkGetInstanceProcAddr) {
            failure = "the Vulkan loader has no vkGetInstanceProcAddr";
            return;
        }
        v.vkCreateInstance = reinterpret_cast<PFN_vkCreateInstance>(
            v.vkGetInstanceProcAddr(nullptr, "vkCreateInstance"));
        v.vkEnumerateInstanceVersion = reinterpret_cast<PFN_vkEnumerateInstanceVersion>(
            v.vkGetInstanceProcAddr(nullptr, "vkEnumerateInstanceVersion"));
        if (!v.vkCreateInstance) failure = "the Vulkan loader has no vkCreateInstance";
    });
    reason = failure;
    return failure.empty();
}

// Probes and device construction are serialized by their caller. Keep the
// loader open for process lifetime, including after a watchdog abandons a device.
inline bool load_instance_functions(VkInstance instance) {
    Runtime &v = vk();
    bool ok = true;
#define WGSE_VK_LOAD(name) \
    v.name = reinterpret_cast<PFN_##name>(v.vkGetInstanceProcAddr(instance, #name)); \
    ok = ok && v.name;
    WGSE_VK_FUNCTIONS(WGSE_VK_LOAD)
#undef WGSE_VK_LOAD
    return ok;
}
#undef WGSE_VK_FUNCTIONS

template <class T>
T make(VkStructureType type) {
    T value{};
    value.sType = type;
    return value;
}

inline void check(VkResult result, const char *what) {
    if (result != VK_SUCCESS)
        throw std::runtime_error(std::string("Vulkan ") + what + " failed (VkResult "
            + std::to_string(int(result)) + ")");
}

struct Buffer {
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    void *mapped = nullptr;
    VkDeviceSize size = 0;
    VkMemoryPropertyFlags properties = 0;
};

} // namespace wgse::vulkan

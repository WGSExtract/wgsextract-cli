"""GPU and host memory planning for Parabricks."""

from dataclasses import dataclass

from ._resource_inputs import GIB, Operation, PreflightEstimate, ResourceEstimate


@dataclass(frozen=True)
class GpuMemory:
    """One GPU as nvidia-smi reports it; memory is None where it reports
    none (N/A, as on unified-memory systems)."""

    index: int
    name: str
    total_bytes: int | None
    free_bytes: int | None


@dataclass(frozen=True)
class ParabricksNeed:
    """Free GPU memory a Parabricks tool needs by default (``full``, or the
    GPU's total less ``headroom`` for tools that size themselves from it) and
    with its low-memory option (``floor``; tools without one need ``full``),
    and the host RAM a whole genome needs either way (``host``), of which
    ``host_floor`` is needed for any input."""

    full: int
    floor: int
    host: int
    option: str | None = None
    headroom: int | None = None
    host_floor: int | None = None


# Parabricks 4.8 needs a GPU with 16 GB (a T4 reports 15360 MiB). Measured on
# an RTX 3090 (24 GiB) with chr20 of a 30x genome (docs/resource_estimates.md):
# fq2bam sizes its streams from the GPU's total memory and peaked at 21.5 GiB,
# so a GPU that other programs share needs --low-memory, which peaked at
# 13.5 GiB; DeepVariant peaked at 12.9 GiB and HaplotypeCaller at 17.2 GiB, or
# 10.7 GiB with --htvc-low-memory. minimap2's 18 GB is NVIDIA's figure.
# Host RAM peaked at 33 GiB for fq2bam (30 GiB in low-memory mode, which a
# 24 GiB container limit kills either way), 24 GiB for DeepVariant and
# 17 GiB for HaplotypeCaller; minimap2's is assumed to match fq2bam's. A whole
# 30x genome's fq2bam peaked at 41 GiB, while sorting.
PB_MIN_GPU_TOTAL = 15 * GIB
PB_LOW_MEMORY_FLOOR = 14 * GIB
PB_GPU_NEEDS = {
    "fq2bam": ParabricksNeed(
        PB_LOW_MEMORY_FLOOR,
        PB_LOW_MEMORY_FLOOR,
        41 * GIB,
        "--low-memory",
        headroom=2 * GIB,
        host_floor=34 * GIB,
    ),
    "minimap2": ParabricksNeed(
        18 * GIB, PB_LOW_MEMORY_FLOOR, 41 * GIB, "--low-memory", host_floor=34 * GIB
    ),
    "haplotypecaller": ParabricksNeed(
        18 * GIB, 12 * GIB, 18 * GIB, "--htvc-low-memory"
    ),
    "deepvariant": ParabricksNeed(PB_LOW_MEMORY_FLOOR, PB_LOW_MEMORY_FLOOR, 25 * GIB),
}
# NVIDIA recommends 100 GB of host RAM for one or two GPUs, 196 GB for four
# and 392 GB for eight (49 GB a GPU).
PB_HOST_RECOMMENDED = 100 * 10**9
PB_HOST_RECOMMENDED_PER_GPU = 49 * 10**9


@dataclass(frozen=True)
class ParabricksPlan:
    """How a Parabricks tool fits the GPUs and the host: whether it needs its
    low-memory option and what falls short."""

    tool: str
    gpus: tuple[GpuMemory, ...]
    host_total: int
    host_available: int

    @property
    def need(self) -> ParabricksNeed:
        return PB_GPU_NEEDS[self.tool]

    @property
    def gpu_free(self) -> int | None:
        """The least free memory of any GPU (pbrun uses them all), if known."""
        free = [g.free_bytes for g in self.gpus if g.free_bytes is not None]
        return min(free) if free and len(free) == len(self.gpus) else None

    def default_need(self, gpu: GpuMemory) -> int:
        """What the tool needs of this GPU without its low-memory option."""
        if self.need.headroom is None or gpu.total_bytes is None:
            return self.need.full
        return max(self.need.full, gpu.total_bytes - self.need.headroom)

    @property
    def busy_gpu(self) -> GpuMemory | None:
        """The GPU furthest below its default need, if any is; each GPU is
        judged by its own reading, whatever the others report."""
        short = [
            g
            for g in self.gpus
            if g.free_bytes is not None and g.free_bytes < self.default_need(g)
        ]
        return min(
            short,
            key=lambda g: (g.free_bytes or 0) - self.default_need(g),
            default=None,
        )

    @property
    def low_memory(self) -> bool:
        return self.need.option is not None and self.busy_gpu is not None

    @property
    def options(self) -> list[str]:
        """The pbrun options this plan adds."""
        return [self.need.option] if self.low_memory and self.need.option else []

    def gpu_need(self, gpu: GpuMemory) -> int:
        return self.need.floor if self.low_memory else self.default_need(gpu)

    @property
    def gpu_needed(self) -> int:
        return max(
            (self.gpu_need(g) for g in self.gpus),
            default=self.need.floor if self.low_memory else self.need.full,
        )

    @property
    def host_needed(self) -> int:
        return self.need.host

    @property
    def host_minimum(self) -> int:
        return self.need.host_floor or self.need.host

    @property
    def host_recommended(self) -> int:
        return max(PB_HOST_RECOMMENDED, PB_HOST_RECOMMENDED_PER_GPU * len(self.gpus))

    def shortfalls(self) -> list[str]:
        """Why the tool can't run here, if it can't."""
        problems = []
        small = [
            g
            for g in self.gpus
            if g.total_bytes is not None and g.total_bytes < PB_MIN_GPU_TOTAL
        ]
        short = [
            g
            for g in self.gpus
            if g.free_bytes is not None and g.free_bytes < self.gpu_need(g)
        ]
        if small:
            problems.append(
                f"Parabricks needs a GPU with 16 GB of memory; GPU "
                f"{small[0].index} has {(small[0].total_bytes or 0) / GIB:.1f} "
                "GiB, so align or call on the CPU instead."
            )
        elif short:
            gpu = min(short, key=lambda g: (g.free_bytes or 0) - self.gpu_need(g))
            problems.append(
                f"Parabricks {self.tool} needs {self.gpu_need(gpu) / GIB:.1f} GiB "
                f"of free GPU memory{' even in low-memory mode' if self.low_memory else ''}"
                f", but GPU {gpu.index} has only {(gpu.free_bytes or 0) / GIB:.1f} "
                "GiB free: close the other programs using the GPU (see nvidia-smi)."
            )
        if self.host_total < self.host_minimum:
            problems.append(
                f"Parabricks {self.tool} needs at least {self.host_minimum / GIB:.0f} "
                f"GiB of RAM, but this computer has {self.host_total / GIB:.1f} "
                "GiB, so run it on the CPU instead."
            )
        return problems

    def host_warning(self) -> str | None:
        """Why the host's RAM may fall short this time, though it has enough."""
        if (
            self.host_available < self.host_needed
            and self.host_minimum <= self.host_total
        ):
            return (
                f"Parabricks {self.tool} needs about {self.host_needed / GIB:.0f} "
                f"GiB of RAM, but only {self.host_available / GIB:.1f} GiB is "
                "available: close other programs, or it may be killed partway."
            )
        return None

    def estimate(self, operation: Operation) -> PreflightEstimate:
        """Rows for report_resources: GPU and host memory."""
        notes = [
            f"Parabricks {self.tool} on {len(self.gpus)} GPU(s): "
            + ", ".join(
                f"{g.name} ({'unknown' if g.free_bytes is None else f'{g.free_bytes / GIB:.1f}'}"
                f" GiB free)"
                for g in self.gpus
            )
        ]
        if self.low_memory:
            notes.append(
                f"Using {self.need.option}: less GPU memory is free than "
                f"{self.tool}'s default needs."
            )
        if self.host_total < self.host_recommended:
            notes.append(
                f"NVIDIA recommends {self.host_recommended / 10**9:.0f} GB of RAM "
                f"for {len(self.gpus) or 1} GPU(s); this host has "
                f"{self.host_total / 10**9:.0f} GB."
            )
        return PreflightEstimate(
            operation,
            (
                ResourceEstimate("gpu", self.gpu_needed, self.gpu_free, {}),
                ResourceEstimate("memory", self.host_needed, self.host_available, {}),
            ),
            tuple(notes),
        )

#pragma once

// Denser suffix-array samples kept in a side file next to a BWA index
// (<prefix>.wgse-sa<interval>), so that locate walks stop sooner. Each sample is
// the position upstream's bwt_sa() returns for its row, so results do not change.

#include "bwt.h"

#if defined(__ARM_FEATURE_CRC32)
#include <arm_acle.h>
#endif
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>

namespace wgse::dense_sa {

constexpr char file_magic[8] = {'W', 'G', 'S', 'E', 'D', 'S', 'A', '1'};

// Little-endian, followed by entries 32-bit low words and words 32-bit words
// of bit 32 (entry i in bit i % 32 of word i / 32).
struct Header {
    char magic[8];
    uint64_t seq_len, primary, sa_intv, shift, entries, words;
    uint64_t l2[5];
    // CRC-32C of the low words, then the high words.
    uint64_t checksum;
};

inline std::string path(const std::string &prefix, uint32_t shift) {
    return prefix + ".wgse-sa" + std::to_string(1u << shift);
}

// What a side file must match: the index's length, primary row, cumulative base
// counts and sampling interval (also in the .sa file's header).
struct Shape {
    uint64_t seq_len = 0, primary = 0, sa_intv = 0;
    uint64_t l2[5] = {};
};

inline Shape shape(const bwt_t &bwt) {
    Shape s;
    s.seq_len = bwt.seq_len;
    s.primary = bwt.primary;
    s.sa_intv = bwt.sa_intv > 0 ? uint64_t(bwt.sa_intv) : 0;
    for (int c = 0; c < 5; ++c) s.l2[c] = bwt.L2[c];
    return s;
}

// Whether an index's samples can be made denser by 1 << shift, with each
// position in 33 bits and each entry indexed in 32.
inline bool supported(const Shape &s, uint32_t shift) {
    const uint64_t intv = s.sa_intv;
    if (!intv || (intv & (intv - 1)) || shift == 0 || shift >= 64 || (uint64_t(1) << shift) >= intv
        || s.seq_len >> 33)
        return false;
    return (s.seq_len / intv + 1) * ((intv >> shift) - 1) <= UINT32_MAX;
}

inline bool supported(const bwt_t &bwt, uint32_t shift) {
    return bwt.sa && bwt.sa_intv > 0 && bwt.n_sa == (bwt.seq_len + bwt.sa_intv) / bwt.sa_intv
        && supported(shape(bwt), shift);
}

inline uint64_t entries(const Shape &s, uint32_t shift) {
    return (s.seq_len / s.sa_intv + 1) * ((s.sa_intv >> shift) - 1);
}

// The row of entry i: every (1 << shift)th row the index does not sample.
inline uint64_t row(uint64_t i, uint64_t sa_intv, uint32_t shift) {
    const uint64_t per = (sa_intv >> shift) - 1;
    return (i / per) * sa_intv + ((i % per + 1) << shift);
}

inline Header header(const Shape &s, uint32_t shift) {
    Header h{};
    memcpy(h.magic, file_magic, sizeof(h.magic));
    h.seq_len = s.seq_len;
    h.primary = s.primary;
    h.sa_intv = s.sa_intv;
    h.shift = shift;
    h.entries = entries(s, shift);
    h.words = (h.entries + 31) / 32;
    for (int c = 0; c < 5; ++c) h.l2[c] = s.l2[c];
    return h;
}

// Whether a file's header is the one written for this index and shift.
inline bool matches(const Header &h, const Header &want) {
    return !memcmp(h.magic, want.magic, sizeof(h.magic)) && h.seq_len == want.seq_len
        && h.primary == want.primary && h.sa_intv == want.sa_intv && h.shift == want.shift
        && h.entries == want.entries && h.words == want.words
        && !memcmp(h.l2, want.l2, sizeof(h.l2));
}

// CRC-32C (Castagnoli), with the ARMv8 CRC instructions where available.
inline uint32_t crc32c(uint32_t crc, const void *data, size_t bytes) {
    const auto *p = static_cast<const uint8_t *>(data);
    crc = ~crc;
#if defined(__ARM_FEATURE_CRC32)
    for (; bytes >= 8; p += 8, bytes -= 8) {
        uint64_t x;
        memcpy(&x, p, 8);
        crc = __crc32cd(crc, x);
    }
    for (; bytes; ++p, --bytes) crc = __crc32cb(crc, *p);
#else
    static const auto table = [] {
        std::array<uint32_t, 256> t{};
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t c = i;
            for (int k = 0; k < 8; ++k) c = c & 1 ? (c >> 1) ^ 0x82F63B78u : c >> 1;
            t[i] = c;
        }
        return t;
    }();
    for (; bytes; ++p, --bytes) crc = table[(crc ^ *p) & 0xff] ^ (crc >> 8);
#endif
    return ~crc;
}

} // namespace wgse::dense_sa

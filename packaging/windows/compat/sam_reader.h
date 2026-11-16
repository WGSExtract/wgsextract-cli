// SPDX-License-Identifier: MIT
#pragma once

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <sys/types.h>

// Owns read-ahead for samblaster's one uninterrupted input stream, not a
// replacement for POSIX getline (whose callers may mix other stdio operations).
class WgseSamReader {
    FILE *stream_;
    char buffer_[65536];
    size_t position_ = 0, end_ = 0;

public:
    explicit WgseSamReader(FILE *stream) : stream_(stream) {}
    WgseSamReader(const WgseSamReader &) = delete;
    WgseSamReader &operator=(const WgseSamReader &) = delete;

    ssize_t read(char **line, size_t *capacity)
    {
        size_t length = 0;
        for (;;) {
            if (position_ == end_) {
                end_ = std::fread(buffer_, 1, sizeof(buffer_), stream_);
                position_ = 0;
                if (!end_) break;
            }
            const char *newline = static_cast<const char *>(
                std::memchr(buffer_ + position_, '\n', end_ - position_));
            const size_t count = newline
                ? static_cast<size_t>(newline - buffer_ - position_) + 1
                : end_ - position_;
            if (count > static_cast<size_t>(std::numeric_limits<ssize_t>::max()) - length) {
                errno = EOVERFLOW;
                return -1;
            }
            const size_t required = length + count + 1;
            if (!*line || *capacity < required) {
                size_t grown = *line && *capacity ? *capacity : 256;
                while (grown < required) {
                    if (grown > std::numeric_limits<size_t>::max() / 2) {
                        grown = required;
                        break;
                    }
                    grown *= 2;
                }
                char *allocation = static_cast<char *>(std::realloc(*line, grown));
                if (!allocation) return -1;
                *line = allocation;
                *capacity = grown;
            }
            std::memcpy(*line + length, buffer_ + position_, count);
            length += count;
            position_ += count;
            if (newline) break;
        }
        if (!length) return -1;
        (*line)[length] = '\0';
        return static_cast<ssize_t>(length);
    }
};

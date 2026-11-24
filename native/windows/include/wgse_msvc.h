/* Force-included into wgse-bwa's C and C++ sources in MSVC-ABI builds
 * (clang-cl): the POSIX shims of ./ and packaging/windows/compat. */
#ifndef WGSE_MSVC_H
#define WGSE_MSVC_H

#include <malloc.h> /* alloca */
#include <sys/stat.h>
#include <unistd.h>
#include "wgse_compat.h"

#ifndef S_ISREG
#define S_ISREG(m) (((m) & S_IFMT) == S_IFREG)
#define S_ISDIR(m) (((m) & S_IFMT) == S_IFDIR)
#endif

/* 64-bit file sizes, as _FILE_OFFSET_BITS=64 gives the MinGW builds: MSVC's
 * struct stat has a 32-bit st_size, so BWA's err_fflush() fstat() of its
 * output fails (EOVERFLOW) once a SAM file passes 2 GiB. */
#define stat _stat64
#define fstat _fstat64

#ifdef __cplusplus
/* BWA's headers declare these outside their extern "C" blocks. ELF and Mach-O
 * don't mangle variable names, but MSVC's C++ does, so declare their C linkage
 * first; the headers' declarations then keep it. */
extern "C" {
extern int bwa_verbose, bwa_dbg;
extern char bwa_rg_id[256];
extern unsigned char nst_nt4_table[256];
}
#endif

#endif
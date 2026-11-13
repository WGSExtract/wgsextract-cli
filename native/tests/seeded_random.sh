# shellcheck shell=sh
# Sourced by the native test scripts. Prefix an awk program with
# "$seeded_random", then call seed(n) (1 <= n < 2147483647) and rnd() in it.
#
# awk's rand() differs between awks, and Ubuntu's mawk ignores srand()'s seed,
# so test data built with it changed on every run there (#769, #806). This
# Park-Miller generator is exact in any awk's doubles (products stay below
# 2^53), so gawk, mawk and BSD awk all build the same data.
# shellcheck disable=SC2034
seeded_random='function seed(s) { state = s }
function rnd() { state = state * 16807 % 2147483647; return state / 2147483647 }
'

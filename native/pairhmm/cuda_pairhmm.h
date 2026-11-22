// SPDX-License-Identifier: MIT
#pragma once

#include <vector>
#include "pairhmm_common.h"

// Raw probabilities, not log likelihoods: GKL retains its own float-to-double
// rescue and host libm rounding. False leaves the entire batch to GKL.
bool wgse_cuda_pairhmm(const std::vector<testcase>& cases, std::vector<float>& results);

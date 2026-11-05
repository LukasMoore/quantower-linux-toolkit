/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Quantower Linux Toolkit contributors.
 * Synthetic regression: use only in a child with core dumps disabled.
 */
#include <assert.h>

int main(void)
{
    __assert_fail("private assertion must not enter the sidecar", "private-file-name", 17, "_XAllocID");
}

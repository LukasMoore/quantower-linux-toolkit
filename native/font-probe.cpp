// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Quantower Linux Toolkit contributors.
// Synthetic checks of fixed setup prerequisites; no app access or font inventory output.
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <gdiplus.h>
#include <stdio.h>

using namespace Gdiplus;
using namespace Gdiplus::DllExports;

struct Family { const wchar_t *name; const char *label; };
static const Family families[] = {
    {L"Verdana", "Verdana"}, {L"Arial", "Arial"},
    {L"Microsoft Sans Serif", "Microsoft Sans Serif"}, {L"Segoe UI", "Segoe UI"},
    {L"Times New Roman", "Times New Roman"}, {L"Courier New", "Courier New"},
    {L"Consolas", "Consolas"}, {L"Tahoma", "Tahoma"}
};

int main() {
    GdiplusStartupInput startup;
    ULONG_PTR token = 0;
    GpStatus started = GdiplusStartup(&token, &startup, NULL);
    if (started != Ok) {
        printf("{\"schema_version\":1,\"synthetic_only\":true,\"passed\":false,"
               "\"startup_status\":%d}\n", (int)started);
        return 2;
    }
    bool passed = true;
    int family_status[8] = {}, style_status[8][4] = {};
    const int styles[] = {FontStyleRegular, FontStyleBold, FontStyleItalic, FontStyleBold | FontStyleItalic};
    for (unsigned i = 0; i < sizeof(families) / sizeof(families[0]); ++i) {
        GpFontFamily *family = NULL;
        family_status[i] = GdipCreateFontFamilyFromName(families[i].name, NULL, &family);
        if (family_status[i] != Ok) passed = false;
        for (unsigned n = 0; n < sizeof(styles) / sizeof(styles[0]); ++n) {
            GpFont *font = NULL;
            style_status[i][n] = family_status[i] == Ok
                ? GdipCreateFont(family, 14.0f, styles[n], UnitPixel, &font) : -1;
            if (style_status[i][n] != Ok) passed = false;
            if (font) GdipDeleteFont(font);
        }
        if (family) GdipDeleteFontFamily(family);
    }
    GdiplusShutdown(token);
    printf("{\"schema_version\":1,\"synthetic_only\":true,\"architecture_bits\":%u,\"passed\":%s,\"families\":[",
           (unsigned)(8 * sizeof(void *)), passed ? "true" : "false");
    for (unsigned i = 0; i < sizeof(families) / sizeof(families[0]); ++i)
        printf("%s{\"requested\":\"%s\",\"family_status\":%d,\"style_statuses\":[%d,%d,%d,%d]}",
               i ? "," : "", families[i].label, family_status[i], style_status[i][0], style_status[i][1],
               style_status[i][2], style_status[i][3]);
    printf("]}\n");
    return passed ? 0 : 1;
}

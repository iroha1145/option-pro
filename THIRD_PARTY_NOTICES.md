# Third-Party Notices

## BreakoutAnalysis

- Repository: https://github.com/calesthio/BreakoutAnalysis
- Reviewed commit: 4e5619ac2a90958217d3d356da7528a96df9c000
- License: MIT
- Copyright: Copyright (c) 2025 BreakoutAnalysis Contributors

Option Pro independently reimplements a small set of discovery concepts from
BreakoutAnalysis: fixed TradingView America screener requests, separate regular
and premarket candidate discovery, bounded mover filters, and candidate
deduplication. BreakoutAnalysis is not bundled, vendored, used as a submodule,
or treated as a confirmation engine.

The following BreakoutAnalysis components are deliberately excluded: its
dependency set, Alpaca quality filter, local JSON state, browser automation,
screenshots, news collection, LLM analysis, Discord, Gmail, fixed-loop
scheduler, price ceiling, technology-sector exemption, and Provider technical
indicators.

The complete license text is preserved in
third_party/BreakoutAnalysis-LICENSE.

## Unicode Character Database

- Source: https://www.unicode.org/Public/17.0.0/ucd/Unihan.zip
- Source file: Unihan_Variants.txt
- Unicode version: 17.0.0
- Source date: 2025-07-24

Option Pro includes a mechanically derived conflict set from the
`kSimplifiedVariant` field. It contains source characters whose simplified
variant list does not include the source character itself. The application
uses this local, read-only set to validate simplified Chinese model output
without a network request.

UNICODE LICENSE V3

COPYRIGHT AND PERMISSION NOTICE

Copyright © 1991-2026 Unicode, Inc.

NOTICE TO USER: Carefully read the following legal agreement. BY DOWNLOADING,
INSTALLING, COPYING OR OTHERWISE USING DATA FILES, AND/OR SOFTWARE, YOU
UNEQUIVOCALLY ACCEPT, AND AGREE TO BE BOUND BY, ALL OF THE TERMS AND CONDITIONS
OF THIS AGREEMENT. IF YOU DO NOT AGREE, DO NOT DOWNLOAD, INSTALL, COPY,
DISTRIBUTE OR USE THE DATA FILES OR SOFTWARE.

Permission is hereby granted, free of charge, to any person obtaining a copy of
data files and any associated documentation (the "Data Files") or software and
any associated documentation (the "Software") to deal in the Data Files or
Software without restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, and/or sell copies of the Data Files
or Software, and to permit persons to whom the Data Files or Software are
furnished to do so, provided that either (a) this copyright and permission
notice appear with all copies of the Data Files or Software, or (b) this
copyright and permission notice appear in associated Documentation.

THE DATA FILES AND SOFTWARE ARE PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT OF THIRD
PARTY RIGHTS.

IN NO EVENT SHALL THE COPYRIGHT HOLDER OR HOLDERS INCLUDED IN THIS NOTICE BE
LIABLE FOR ANY CLAIM, OR ANY SPECIAL INDIRECT OR CONSEQUENTIAL DAMAGES, OR ANY
DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
CONNECTION WITH THE USE OR PERFORMANCE OF THE DATA FILES OR SOFTWARE.

Except as contained in this notice, the name of a copyright holder shall not be
used in advertising or otherwise to promote the sale, use or other dealings in
these Data Files or Software without prior written authorization of the
copyright holder.

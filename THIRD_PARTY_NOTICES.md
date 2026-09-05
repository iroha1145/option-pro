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

## Interface and motion references

The following official sources were reviewed on 2026-09-05. The commits identify
the versions consulted during this review, not the original provenance of
historical adaptations. Implementation guidance is recorded in
`docs/UI_DESIGN_SYSTEM.md`. Reference sites are not contacted by the application
at runtime, and no paid assets or Pro components are included by this review.

| Source | Reviewed commit | Project use | License |
| --- | --- | --- | --- |
| [Beautiful UI](https://www.beautifului.dev/) / [slev12397/beautiful-ui](https://github.com/slev12397/beautiful-ui) | `06557d7ff33a1eb70d5987bae9ac4c70fa0e20c4` | Adapted insight-card, chart hierarchy and search feedback patterns | MIT; copyright (c) 2026 Shane Levine |
| [beUI](https://beui.dev/) / [starc007/ui-components](https://github.com/starc007/ui-components) | `04d6f76e9e67e35cded996b1b8d08a5ddcebc13a` | Adapted shared-layout selection and button feedback patterns; project spring values may differ | MIT; copyright (c) 2026 Saurabh Chauhan |
| [Rare UI](https://www.rareui.com/) / [swamimalode07/rare-ui](https://github.com/swamimalode07/rare-ui) | `b3efd6c290884a852b7af39d34df99a762dbbf3f` | Reference for final-value screen-reader text, initial numeric state and reduced-motion scrolling; no complete counter or scroll-progress component vendored | MIT; copyright (c) 2026 Swami Malode |
| [Transitions.dev](https://transitions.dev/) / [Jakubantalik/transitions.dev](https://github.com/Jakubantalik/transitions.dev) | `74e572345d809f981250938208bd991314c2e780` | Existing CSS motion tokens and transition recipes, with project-specific orchestration and styling | Transition usage terms, described below |
| [shadcn/ui](https://ui.shadcn.com/) / [shadcn-ui/ui](https://github.com/shadcn-ui/ui) | `7c9eaba1c0a6404c990c144a654792e3313c650d` | Semantic theme mapping and adapted Select composition using Radix Select, including its portal, item and focus behavior | MIT; copyright (c) 2023 shadcn |

### MIT-licensed interface references

The following MIT terms apply separately to the Beautiful UI, beUI, Rare UI and
shadcn/ui material identified above, under each source's respective copyright
notice. Complete copies from the reviewed repositories are also preserved in
`third_party/BeautifulUI-LICENSE`, `third_party/beUI-LICENSE`,
`third_party/RareUI-LICENSE` and `third_party/shadcn-ui-LICENSE`.

MIT License

Copyright (c) 2026 Shane Levine — Beautiful UI

Copyright (c) 2026 Saurabh Chauhan — beUI

Copyright (c) 2026 Swami Malode — Rare UI

Copyright (c) 2023 shadcn — shadcn/ui

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

### Transitions.dev transition snippets

Official terms: https://transitions.dev/terms.html (page last updated July 2026;
reviewed 2026-09-05). Transition snippets are covered by the site's transition
usage terms, not the MIT license applied to its CLI and Refine tooling.

The terms permit use and modification of accessible snippets in personal and
commercial applications. They prohibit repackaging or redistributing the
collection, or a substantial part of it, as a competing transition library,
template pack or component kit. Option Pro uses the recipes as part of its
application interface. This review did not obtain or add paid Pro recipes.

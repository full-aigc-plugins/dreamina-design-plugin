# Third-Party Notices

This compatibility foundation does not bundle third-party application binaries,
proprietary SDKs, credentials, or generated media. The expressly attributed
Apache-2.0 ReelBench Skill source below is the sole vendored-source exception.

References to Blender, Autodesk Maya, Dreamina, Seedance, Codex, and other product names identify interoperability targets. Their trademarks and software remain the property of their respective owners.

The deterministic reference-video analysis design was informed by the publicly
available ReelBench video-shots skill at commit
`75520c7b32ab5af8b22c5e4f79705efbbc0d8e07`. That earlier independent Python
implementation did not copy or bundle ReelBench source code or media.

This plugin vendors the `video-shots` and `video-sync` Skill trees from
[`eternityspring/reelbench-skills`](https://github.com/eternityspring/reelbench-skills)
at commit `18f2f63987337df0975a89973d38d50f3231ee31`, under the Apache License
2.0. They are packaged respectively as `dreamina-video-shots` and
`dreamina-video-sync`; apart from those frontmatter names and directories, the
vendored Skill files are byte-for-byte identical to the pinned upstream tree.
No ReelBench demo media is bundled.

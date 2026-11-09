# Changelog

## Unreleased — experimental source checkpoint

- Add default-off Wine 0010 native fullscreen-placement policy and 0011 caption-move
  policy to the pinned source recipe. Existing published runtime assets are unchanged.
- **Known 0011 regression:** custom fullscreen, minimize, maximize and close controls
  can be bypassed when they share `HTCAPTION` with the header. Distinct `HTCLOSE`
  fixture results do not validate custom chrome. This remains an incomplete checkpoint.
- Document exact opt-in variables, bounded optional trace and validation limits in
  [Native window authority](docs/NATIVE-WINDOW-AUTHORITY.md).

Original toolkit changes and tests were largely AI-generated. Review and test an
isolated demo installation; passing tests does not establish production readiness.

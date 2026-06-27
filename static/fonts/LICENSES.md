# PaperCore Font Assets

This directory is reserved for self-hosted web fonts. PaperCore should not load
fonts from Google Fonts CDN, so the app remains local-first and works offline.

Expected files:

- `PlayfairDisplay-Bold.woff2`
- `EBGaramond-Regular.woff2`
- `EBGaramond-Italic.woff2`
- `NotoSerifSC-Regular.woff2`

License note:

- Playfair Display, EB Garamond, and Noto Serif SC are distributed under the
  SIL Open Font License 1.1.
- Keep the original `OFL.txt` files with the downloaded/subset fonts before
  distributing the project.

Current status:

- The CSS already defines serif fallback stacks.
- The current sandbox cannot resolve `fonts.googleapis.com`, so the binary
  `.woff2` files have not been downloaded in this pass.

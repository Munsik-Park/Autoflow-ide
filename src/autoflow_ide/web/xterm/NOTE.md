# xterm.js, as kept here (#86)

The browser page draws a session's screen with xterm.js. These files are copied byte for byte from the npm package
`@xterm/xterm` 6.0.0 (MIT, https://github.com/xtermjs/xterm.js), so the page loads nothing from another origin and
works without a network:

- package tarball: `https://registry.npmjs.org/@xterm/xterm/-/xterm-6.0.0.tgz`
- `dist.integrity`: `sha512-TQwDdQGtwwDt+2cgKDLn0IRaSxYu1tSUjgKarSDkUM0ZNiSRXFpjxEsvc/Zgc5kq5omJ+V0a8/kIM2WD3sMOYg==`

| File here | From the package | SHA-256 |
|---|---|---|
| `xterm.js` | `lib/xterm.js` | `14903579ff54664cd72f8e8699e6961a6272c21863ec1c3b118cdc8af5d4a972` |
| `xterm.css` | `css/xterm.css` | `854a7c0fb70e8b1a083c16797ab827299fb18744f5ad34f227b48337e33293c6` |
| `LICENSE` | `LICENSE` | `b569f629d00f2626a8100df2a1798210535621e42164dfd426a6fe5aac7b0ccd` |

To update: take the files from the new release's tarball, check its `dist.integrity`, and update this table.

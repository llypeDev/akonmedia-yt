# Vendored assets

Files here are third-party builds, kept in the repository so the player works
without a CDN and without a node build step.

| File | Version | License | Source |
|---|---|---|---|
| `hls.min.js` | 1.6.15 | Apache-2.0 | https://github.com/video-dev/hls.js |
| `fonts/inter-latin-var.woff2` | Inter 4.001 | OFL-1.1, `fonts/Inter-OFL.txt` | https://github.com/rsms/inter |
| `fonts/space-grotesk-latin-var.woff2` | Space Grotesk 2.000 | OFL-1.1, `fonts/SpaceGrotesk-OFL.txt` | https://github.com/floriankarsten/space-grotesk |
| `fonts/jetbrains-mono-latin-var.woff2` | JetBrains Mono 2.211 | OFL-1.1, `fonts/JetBrainsMono-OFL.txt` | https://github.com/JetBrains/JetBrainsMono |
| `../icons/sprite.svg` | lucide-static 1.59.0 | ISC (some icons MIT), `LUCIDE-LICENSE.txt` | https://lucide.dev |

The license banner is preserved at the top of `hls.min.js`. Do not edit the body;
replace the whole file when upgrading.

The fonts and the icon sprite are generated, not copied by hand:
`tools/build_logo.py` downloads the upstream releases, subsets the fonts to
Latin (Portuguese and the other Western European languages) as variable
woff2, and packs the Lucide icons into one `<symbol>` sprite. Regenerate them
with that script instead of editing the output.

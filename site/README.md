# Project website

Static site for "Learning the Cost of Rerouting". Plain HTML, CSS and JavaScript,
no build step, no backend. Open `index.html` in a browser or serve the folder:

```sh
cd site && python3 -m http.server 8000
# then open http://localhost:8000
```

## Files

| File | Purpose |
|---|---|
| `index.html` | The whole page: problem, research question, prior work, controllers, metrics, roadmap, results, team |
| `styles.css` | Design tokens (light and dark), layout, the CSS-animated diagrams |
| `main.js` | Theme toggle, reduced-motion handling, and the results loader |
| `results/results.json` | The only file later chunks need to edit to publish results |
| `vercel.json` | Clean URLs and cache headers for Vercel |

## Dropping in results (Chunks 2 to 6)

The Results section reads `results/results.json` at load time. To publish a result:

1. Have the analysis script in `analysis/` write the figure (PNG or SVG) into
   `site/results/`, for example `site/results/gate_timeseries.svg`.
2. In `results/results.json`, find the figure by `id` and set `"src"` to that
   path relative to `site/`, for example `"results/gate_timeseries.svg"`.
   Update `caption` and `script` if they changed.
3. Add headline numbers to the `numbers` array, each with the script that produced it:
   ```json
   { "label": "Goodput deficit per move (slow→fast, 6 ms gap)", "value": "1.9", "unit": "MB", "source": "analysis/gate.py" }
   ```
4. Set `updated` to the date, and `status.headline` / `status.note` to the current chunk.

Figures with `"src": null` render as placeholders that name the chunk and script
that will produce them. Nothing on the page is typed by hand; the evidence rules in
the repository's CLAUDE.md apply to this file too.

## Deploying on Vercel

The site has no build step. The repository root has a `vercel.json` that tells
Vercel to skip the build and serve `site/` as static files, so a default import
works with no settings changed:

1. Go to https://vercel.com/new and import `MuhammadMuthar/sdn-reroute` from GitHub.
2. Leave Root Directory as `./` and click Deploy.

Vercel auto-detects a "Python" preset because of the repository's
`requirements.txt`; the root `vercel.json` (`"framework": null`,
`"outputDirectory": "site"`) overrides that. If a project was already created
with the Python preset and failed with `PYTHON_ENTRYPOINT_NOT_FOUND`, either
redeploy after this file is on `main`, or set Framework Preset to **Other** and
Root Directory to `site` under Project Settings, then redeploy.

Every push to `main` redeploys; pull requests get preview URLs. Both `vercel.json`
files add clean URLs and a short cache on `results/` so a new figure shows up
within a minute of merging.

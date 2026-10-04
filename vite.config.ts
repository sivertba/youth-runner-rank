import { defineConfig } from 'vite'

// Relative base so the built bundle works from any path on GitHub Pages
// (`https://<user>.github.io/<repo>/`) as well as from a domain root, without
// hardcoding the repository name. The app has no client-side router, only
// in-page anchors, so relative asset URLs are sufficient.
export default defineConfig({
  base: './',
  build: {
    // generated.json is a single ~400 KiB JSON module imported by src/model.
    // Keeping it as one chunk avoids a waterfall, but the size warning is noise
    // for a dataset that is the product.
    chunkSizeWarningLimit: 1024,
  },
})

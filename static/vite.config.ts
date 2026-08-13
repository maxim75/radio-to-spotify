import { defineConfig } from 'vite'
import fs from 'fs';
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    {
      name: 'copy-placeholder',
      generateBundle(options, bundle) {
        // This will copy the placeholder image
        const source = fs.readFileSync("../static/placeholder-album.png");
        this.emitFile({
          type: "asset",
          fileName: 'placeholder-album.png',
          source: source
        });
      }
    }
  ],
  build: {
    outDir: '../static/dist',
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: 'ts/main.tsx',
      output: {
        entryFileNames: '[name].js',
        chunkFileNames: '[name].[hash].js',
        assetFileNames: '[name].[hash][extname]'
      }
    }
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8001'
    }
  }
})
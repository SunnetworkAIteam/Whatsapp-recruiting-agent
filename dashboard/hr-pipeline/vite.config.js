import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Allows the dev server to be accessed through an ngrok tunnel (or
    // any other proxy/tunnel host) for sharing the dashboard without a
    // full deployment. Vite blocks unrecognized Host headers by default
    // as a security measure against DNS rebinding attacks.
    allowedHosts: [".ngrok-free.dev", ".ngrok.io", ".ngrok.app"],
  },
})
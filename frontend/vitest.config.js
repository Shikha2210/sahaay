import {defineConfig} from 'vitest/config';
// Kept separate from vite.config.js so the PWA/build configuration is untouched.
export default defineConfig({test:{environment:'jsdom',setupFiles:['./src/test/setup.js'],include:['src/**/*.test.js','src/**/*.test.jsx']}});

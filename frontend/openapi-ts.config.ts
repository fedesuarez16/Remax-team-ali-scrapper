import { defineConfig } from '@hey-api/openapi-ts'
export default defineConfig({
  input: 'http://localhost:8000/openapi.json',
  output: { path: 'types/api', format: 'prettier' },
  plugins: ['@hey-api/client-fetch'],
})

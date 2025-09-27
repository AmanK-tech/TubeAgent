/// <reference types="vite/client" />

// Optional: declare your custom Vite env vars for better IntelliSense
interface ImportMetaEnv {
  readonly VITE_API_URL?: string
}
interface ImportMeta {
  readonly env: ImportMetaEnv
}


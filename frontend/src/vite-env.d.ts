/// <reference types="vite/client" />

/** The environment variables this app reads, declared so `tsc` can see them. */
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

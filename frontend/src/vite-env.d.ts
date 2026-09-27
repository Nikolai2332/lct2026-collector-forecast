/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_USE_MOCKS?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare module 'echarts/lib/i18n/langRU.js' {
  const lang: Parameters<typeof import('echarts/core').registerLocale>[1];
  export default lang;
}

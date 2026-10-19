// Проверка скриптов приложения: npx eslint leadscout/web/static
export default [
  {
    files: ['leadscout/web/static/**/*.js'],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      globals: {
        window: 'readonly', document: 'readonly', navigator: 'readonly', location: 'readonly', history: 'readonly',
        localStorage: 'readonly', fetch: 'readonly', EventSource: 'readonly', URL: 'readonly', URLSearchParams: 'readonly',
        Intl: 'readonly', setTimeout: 'readonly', clearTimeout: 'readonly', setInterval: 'readonly', clearInterval: 'readonly',
        console: 'readonly', self: 'readonly', caches: 'readonly', Promise: 'readonly',
      },
    },
    rules: {
      'no-unused-vars': ['error', { args: 'after-used', caughtErrors: 'none' }],
      'no-undef': 'error',
      'no-empty': ['error', { allowEmptyCatch: false }],
      eqeqeq: ['error', 'always', { null: 'ignore' }],
      'no-var': 'error',
      'prefer-const': 'error',
    },
  },
];

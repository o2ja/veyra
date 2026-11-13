import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import security from "eslint-plugin-security";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "src/api/schema.d.ts"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.strict, security.configs.recommended],
    languageOptions: { globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "no-restricted-globals": ["error", "localStorage", "sessionStorage"],
      // typed index access (record[key]) is the common case in TS; this rule flags every one of them
      "security/detect-object-injection": "off",
    },
  },
  {
    // Playwright specs read their own fixture files and build matchers from test data
    files: ["e2e/**/*.ts"],
    rules: { "security/detect-non-literal-fs-filename": "off", "security/detect-non-literal-regexp": "off" },
  },
);

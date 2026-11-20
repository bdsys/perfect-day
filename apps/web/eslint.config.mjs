import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

// eslint-config-next 16 ships flat configs, so no FlatCompat layer is needed.
export default [
  ...nextCoreWebVitals,
  ...nextTypescript,
];

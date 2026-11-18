import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

// eslint-config-next 16 ships flat configs, so no FlatCompat layer is needed.
export default [
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    // React Compiler readiness rules, new in eslint-plugin-react-hooks 7. They flag
    // patterns that are correct without the compiler (which this app does not use),
    // such as the latest-value ref. 67 hits on 2026-11-18; adopting them, e.g. with
    // useEffectEvent, is tracker row 61.
    rules: {
      "react-hooks/refs": "off",
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/preserve-manual-memoization": "off",
      "react-hooks/globals": "off",
    },
  },
];

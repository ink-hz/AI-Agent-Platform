# Cloud web-builder fixture dependency fix

The first actual cloud build of `ac8c27588b2962ba08d00c11545eada370613908` failed at `npm run build` because TypeScript includes `webui/src` tests, and three test files import four checked-in shared JSON fixtures from `backend/tests/fixtures/hr_intelligence_company`. The Docker web-builder copied only `webui`, so the relative import targets were absent.

The fix adds one exact four-file COPY into `/src/backend/tests/fixtures/hr_intelligence_company/` before the existing `RUN npm run build`. It does not copy the rest of backend, extraction scripts or provenance files. `webui/tsconfig.json` still includes all `src` tests; package scripts remain `tsc -b && vite build`. The runtime stage is byte-identical to the parent and receives only the existing `web-builder/dist` output, not these test fixtures.

The existing image-contract test globally forbade `copy backend/tests` in any stage, causing a separate observed failure after the Docker fix. It now permits only the four exact backend source paths in the builder, checks destination/order, and still forbids `backend/tests` anywhere in runtime. The existing `.git` and secret-material exclusions remain. This resolves the semantic stage distinction; COPY syntax was not changed to evade the old string assertion.

Evidence:

- `red/`: fresh temporary `/src/webui` layout from337 tracked webui files, no backend files, runs the unchanged `npm run build`. Exit1, the exact same seven TS2307 lines as the original cloud `remote-stdout.log`.
- `green/`: same337 webui input bytes plus the four Docker-declared fixture copies; unchanged `npm run build` exits0. TypeScript passes, Vite7.3.6 transforms5262 modules and finishes in3.89s. Existing large-chunk warning remains; it is not a failed build.
- `runtime-contract.log` / command: original contract test fails on the builder fixture COPY. The original test file bytes are retained with their name marked RED.
- `runtime-contract-green.log` / command: narrowed stage-aware existing image contract passes,1 passed.
- `verification.json`: matching local/cloud diagnostic proof, unchanged webui inputs, unchanged runtime-stage bytes, exact fixture count, patch/Dockerfile/remote-log hashes.
- `fix.patch`: full-index binary diff for the only two changed implementation/test files. `red/` and `green/` preserve Dockerfile/package/tsconfig/lock bytes and per-input hashes; GREEN preserves the exact four fixture bytes and generated-dist hashes.

This local reproduction used macOS Node26.5.0/npm11.17.0 and the existing installed node_modules, deliberately disclosed in command receipts. It did not run fresh npm ci or the Alpine Node24.7.0 Docker image and does not certify the subsequent cloud build. It validates the missing-file layout cause and full unchanged frontend build locally. Root owns the actual immutable-SHA cloud rebuild. No production state, HR runtime/role, original stage-build script or test type-check inclusion was changed by this child.

 Below is a step‑by‑step plan that gives an agent (or you) everything needed to locate, understand, and improve the
  low‑hanging‑performance, memory‑, and security‑issues you listed. The plan is organized by area (backend, database,     API, frontend, concurrency, error handling) and for each area it specifies:
                                                                                                                          What to look for: 1. Array‑based “any‑match” logic
  Typical file locations / patterns: src/**/*.js, src/**/*.ts, any *.match(...).some or for…of loops that iterate whole
    collections to find a single element.
  Why it hurts: O(N) scans for look‑ups that could be O(1).
  Suggested replacement/approach: Replace with a Set/Map lookup or a hash‑indexed object.
  ────────────────────────────────────────
  What to look for: 2. Inefficient string concatenation / template building
  Typical file locations / patterns: utils/*.js, any += on large strings inside loops.
  Why it hurts: Repeated allocations → extra GC pressure.
  Suggested replacement/approach: Use array push + join or template literals built once.
  ────────────────────────────────────────
  What to look for: 3. Repeated DB round‑trips
  Typical file locations / patterns: services/**/*.js, repositories/**/*.ts – look for await Model.find… inside loops.
  Why it hurts: N × latency, can be tens of ms per call.
  Suggested replacement/approach: Batch queries, use $in, SELECT … WHERE id IN (…), or Promise.all on independent calls.  ────────────────────────────────────────
  What to look for: 4. N+1 query patterns
  Typical file locations / patterns: ORM usage (e.g., Sequelize, TypeORM, Prisma) in controllers/*.
  Why it hurts: One query per row → massive overhead.
  Suggested replacement/approach: Use eager loading / include, join, or a single SELECT with proper projection.
  ────────────────────────────────────────
  What to look for: 5. Hard‑coded URLs / endpoint strings
  Typical file locations / patterns: routes/*.js, any axios.get('http://…') or fetch('/api/v1/foo').
  Why it hurts: Prevents config‑driven routing, forces redeploy for changes.
  Suggested replacement/approach: Move to a config file (config/*.json or env vars) and reference via a constants
  module.
  ────────────────────────────────────────
  What to look for: 6. Synchronous‑blocking IO
  Typical file locations / patterns: fs.readFileSync, crypto.pbkdf2Sync, child_process.execSync.
  Why it hurts: Blocks the event loop, adds latency per request.
  Suggested replacement/approach: Switch to async (fs.promises.readFile, crypto.pbkdf2 async, spawn with callbacks).
  ────────────────────────────────────────
  What to look for: 7. Large in‑memory caches without eviction
  Typical file locations / patterns: cache/*, global objects storing whole result sets.
  Why it hurts: Unbounded memory growth → GC pauses.
  Suggested replacement/approach: Use LRU cache (e.g., lru-cache) with size/TTL limits.
  ────────────────────────────────────────
  What to look for: 8. Race‑condition prone mutable globals
  Typical file locations / patterns: Any module that mutates a top‑level variable, e.g., let state = {} exported and
    altered elsewhere.
  Why it hurts: Concurrent requests may clobber each other → extra retries / failures.
  Suggested replacement/approach: Make state immutable; pass data via request‑scoped objects or use a proper
    concurrency‑safe store (Redis, in‑process lock).
  ────────────────────────────────────────
  What to look for: 9. Uncaught promise rejections / missing try/catch
  Typical file locations / patterns: Controllers, middleware that await without try.
  Why it hurts: Errors bubble to the process, trigger restart, or return generic 500s.
  Suggested replacement/approach: Wrap each async handler with a generic error‑wrapper or use an express‑style next(err)
    middleware.
  ────────────────────────────────────────
  What to look for: 10. Verbose, non‑user‑friendly error messages
  Typical file locations / patterns: res.status(500).send(err) or console.error(err) directly to client.
  Why it hurts: Leaks internal details, hurts UX.
  Suggested replacement/approach: Map internal errors to HTTP status codes + clean messages; log details server‑side
    only.
  ────────────────────────────────────────
  What to look for: 11. Frontend rendering loops / unnecessary re‑renders
  Typical file locations / patterns: React/Vue components under src/components/**/*.tsx|jsx|vue. Look for array .map
    inside render without useMemo/React.memo.
  Why it hurts: Each render recomputes expensive structures → UI lag (10‑30 ms).
  Suggested replacement/approach: Memoize derived data, split component, use shouldComponentUpdate/React.memo.
  ────────────────────────────────────────
  What to look for: 12. Large CSS/JS bundles
  Typical file locations / patterns: public/, src/assets/, webpack config.
  Why it hurts: Increases download time, affects TTFB.
  Suggested replacement/approach: Enable code‑splitting, tree‑shaking, compress with gzip/brotli.
  ────────────────────────────────────────
  What to look for: 13. Blocking third‑party requests
  Typical file locations / patterns: Service calls to external APIs inside request handling flow.
  Why it hurts: Adds external latency to your endpoint.
  Suggested replacement/approach: Fire‑and‑forget, cache responses, or move to background worker (e.g., Bull, Sidekiq).
  ────────────────────────────────────────
  What to look for: 14. Missing HTTP caching headers
  Typical file locations / patterns: Controllers that serve static data repeatedly.
  Why it hurts: Clients re‑download unchanged payloads.
  Suggested replacement/approach: Add Cache‑Control, ETag, Last‑Modified where appropriate.
  ────────────────────────────────────────
  What to look for: 15. Improper indexing in DB schema
  Typical file locations / patterns: migrations/*.sql, schema.prisma.
  Why it hurts: Full table scans for common filters → 10 ms → 100 ms+.
  Suggested replacement/approach: Add indexes on columns used in WHERE, JOIN, ORDER BY.
  ────────────────────────────────────────
  What to look for: 16. Use of any/Object in TypeScript
  Typical file locations / patterns: src/**/*.ts.
  Why it hurts: Prevents compile‑time optimizations, can hide bugs that become runtime errors.
  Suggested replacement/approach: Refine types, enable noImplicitAny.
  ────────────────────────────────────────
  What to look for: 17. Long‑running synchronous loops
  Typical file locations / patterns: Any for (let i=0;i<largeArray.length;i++) that does heavy work per iteration.
  Why it hurts: Blocks the event loop → request latency spikes.
  Suggested replacement/approach: Chunk work with setImmediate or move to a worker thread / background job.
  ────────────────────────────────────────
  What to look for: 18. Missing rate‑limiting / throttling
  Typical file locations / patterns: API gateway or middleware not present.
  Why it hurts: Allows abusive traffic that degrades performance for all users.
  Suggested replacement/approach: Add rate‑limit middleware (e.g., express-rate-limit).
  ────────────────────────────────────────
  What to look for: 19. Unoptimized image handling
  Typical file locations / patterns: Upload endpoints, public/images/*.
  Why it hurts: Storing full‑size images hurts storage & bandwidth.
  Suggested replacement/approach: Resize/compress on upload, serve via CDN.
  ────────────────────────────────────────
  What to look for: 20. Lack of profiling / metrics
  Typical file locations / patterns: No prom-client, newrelic, or custom timers.
  Why it hurts: You can’t see where the 10 ms wins are.
  Suggested replacement/approach: Instrument critical paths, capture latency histograms, export to Grafana/Prometheus.

  ---
  Execution Roadmap

  Step: A. Inventory
  Action: List all source directories (src/, services/, controllers/, frontend/).
  Tool(s) to use: Glob (**/*.js, **/*.ts, **/*.tsx)
  Outcome: File list for subsequent greps.
  ────────────────────────────────────────
  Step: B. Pattern Search
  Action: Run targeted Grep to locate each of the patterns above. Example: grep -R \"\\.some(.*)\" src/ for array‑match;
    grep -R \"readFileSync\" . for sync IO.
  Tool(s) to use: Grep (multiple calls, one per pattern)
  Outcome: Files/lines that need review.
  ────────────────────────────────────────
  Step: C. Static Analysis
  Action: Run ESLint/TSLint with performance rules (no-sync, no-array-loop, prefer-set) and security rules
    (no-hardcoded-urls, no-unhandled-rejections).
  Tool(s) to use: Bash (eslint . --rule ...) or use existing CI linter output.
  Outcome: Linter violations list.
  ────────────────────────────────────────
  Step: D. Runtime Profiling
  Action: Spin up the service locally with a request‑benchmark tool (e.g., autocannon or hey) hitting each endpoint.
    Capture latency, CPU, memory.
  Tool(s) to use: Bash (autocannon http://localhost:3000/api/...)
  Outcome: Baseline performance numbers, pinpoint hot paths.
  ────────────────────────────────────────
  Step: E. Database Query Audit
  Action: Enable query logging (e.g., PG_LOG=all) and capture the longest queries while running the benchmark.
  Tool(s) to use: Bash (export PG_LOG=all && npm run dev)
  Outcome: Identify N+1 and missing indexes.
  ────────────────────────────────────────
  Step: F. Concurrency Check
  Action: Run a load test (e.g., hey -c 50 -n 1000 /api/...) and monitor for race‑condition symptoms (duplicate writes,
    inconsistent responses).
  Tool(s) to use: Bash + watch for error logs.
  Outcome: List of race‑prone sections.
  ────────────────────────────────────────
  Step: G. Error‑Message Review
  Action: Search for res.send(err) or throw err that reaches the client.
  Tool(s) to use: Grep (throw err / res\.status(.*).send(err))
  Outcome: Catalog of user‑facing raw errors.
  ────────────────────────────────────────
  Step: H. Frontend Profiling
  Action: Open the UI in Chrome DevTools, record a performance trace while interacting with critical paths. Look for
  long
    JS execution, layout thrash, large paint times.
  Tool(s) to use: Manual (guide user) or instruct npm run storybook && chrome://tracing.
  Outcome: Identify components needing memoization or code‑splitting.
  ────────────────────────────────────────
  Step: I. Create Refactor Tickets
  Action: For each identified issue, open a ticket (or create a TODO comment) with: file, line, current pattern,
  proposed
    change, estimated effort.
  Tool(s) to use: TaskCreate (optional) or just a markdown summary.
  Outcome:
  ────────────────────────────────────────
  Step: J. Iterative Fix & Benchmark
  Action: Apply the fix, run the same benchmark, compare latency/memory. Record delta.
  Tool(s) to use: Edit/Write for code changes, then repeat steps D‑H.
  Outcome:
  ────────────────────────────────────────
  Step: K. Document
  Action: Update README or ops/PROFILE.md with the new performance baseline, profiling commands, and any new config
    (e.g., env vars for cache size, DB indexes).
  Tool(s) to use: Write (new file).
  Outcome:
  ────────────────────────────────────────
  Step: L. Automate Checks
  Action: Add ESLint rules / pre‑commit hook to catch re‑introduction of the patterns.

  ---
  Deliverables (what the agent should produce)

  1. Report – a concise markdown file (OPTIMIZATION_PLAN.md) containing:
    - Summary of identified hotspots per category.
    - Exact file paths & line numbers (from grep output) for each hotspot.
    - Suggested code changes (e.g., “replace array.includes with Set.has”, “batch this query into a single SELECT … WHERE id IN (…)”).
  2. Benchmark baseline – a JSON file (benchmark_before.json) with endpoint latencies, DB query times, memory usage.
  3. Refactor tickets – either a markdown checklist or individual task entries (via TaskCreate) that can be assigned and tracked.
  4. Post‑fix benchmark – the same format (benchmark_after.json) to clearly show the 10 ms (or larger) improvements.
  5. CI lint rule configuration – updated ESLint config enabling the performance/security rules identified.
  6. Documentation – a short “Performance & Security Guidelines” section added to the repo’s onboarding docs.

  ---
  How an Agent Should Use This Plan

  1. Execute Steps A‑H automatically using the listed tools (Glob, Grep, Bash, etc.).
  2. Collect results (file lists, grep matches, benchmark logs) and store them in temporary files.
  3. Generate the markdown report (OPTIMIZATION_PLAN.md) and the two benchmark JSON files.
  4. Create task entries for each hotspot (using TaskCreate) so a developer can pick them up.
  5. Apply CI lint updates via the update-config skill, then run the lint to verify no new violations.
  6. Return the report and a brief “next‑steps” summary to the user.
# hack-b6e61115-memory-munchers
Hackathon team repository for Memory Munchers

# Skyline UI

This dependency-free frontend opens to the product catalog, with a link to Agent chat.

## Product consultant and basket

The catalog now has a floating **Помощник** chat dialog and a **Спросить о товаре** action on each product. The assistant searches the entire database, reads specifications and document links, suggests possible alternatives, and prepares basket additions. Replies stream into the dialog; the conversation survives page refreshes. The basket is at `/basket.html`.

Basket additions require the customer's **Подтвердить добавление** button. The AI has no tool for committing additions. The server rechecks prices, quantities, order multiples, order limits, and available inventory before applying a proposal. Proposals expire after 10 minutes. Changed proposals must be refreshed and confirmed again. Duplicate confirmation requests cannot add an item twice. Basket quantity updates use a version to detect changes from another tab. A basket currently supports 20 distinct products per shopper.

The `product-consultant` agent is registered automatically alongside the existing configured agents. It reuses the Responses API runner with a separate `Shopping` output/reasoning/time budget. The last 12 complete conversation turns are supplied to the model; saved conversation history remains available in the UI. This is a PoC with no authentication: every client shares a single shopper, so all sessions and the basket are shared. CORS allows any origin, header and method.

### Database migration

`20260923105301_AddProductShopping` adds shopper ownership, message deduplication, baskets, proposals, private attachments, chat events, inventory records, and an indexed product search column. It enables PostgreSQL's `pg_trgm` extension for catalog search. Migrations run on API startup. To apply explicitly from the repository root:

```powershell
dotnet ef database update --project MemoryMunchers/MemoryMunchers --configuration Release
```

The database role must be able to install `pg_trgm`, or an administrator can install the extension first. This migration does not seed or invent numeric stock balances.

### Configuration and business data

Configure `Shopping` in `MemoryMunchers/MemoryMunchers/appsettings.json`, or use environment variables such as `Shopping__RequireVerifiedStock`:

| Setting | Default / behavior |
| --- | --- |
| `RequireVerifiedStock` | `false`: catalog demo mode. `Купить` and `Под заказ` are catalog labels, not verified stock. Website order limits are validated separately. |
| `InventoryMaxAgeMinutes` | `15`. Older inventory is treated as unknown. |
| `ProposalLifetimeMinutes` | `10`. |
| `AttachmentLifetimeHours` | `24`. Expired uploads are deleted periodically. |
| `MaxOutputTokens`, `ReasoningEffort`, `RunTimeoutSeconds` | `4096`, `low`, `90`; separate from the generic agent settings. |
| `PurchaseConditions` | Merchant-supplied delivery, payment, return, and business purchasing text. Until supplied, the assistant says this information is unavailable. |
| `PurchaseConditionsSourceUrl` | Optional HTTP(S) source link for the merchant's policies. |
| `CheckoutUrl` | Optional HTTP(S) checkout destination. With no destination configured, the basket remains a review page and does not submit orders. |

For verified inventory, integrate the merchant's stock feed with `ProductInventory` (`ProductId`, `AvailableQuantity`, `CheckedAt` in UTC), populate each product's `Unit`, and enable `RequireVerifiedStock`. This mode rejects additions if stock is unknown/stale or the sales unit is missing. The supplied CSV has no sales units or numeric stock; only 14 products have document links. The assistant does not invent missing data or assume every document is a certificate. Prices and catalog availability are snapshots, and the existing CSV import still adds missing products only. A production feed must refresh prices and product data as well as inventory.

Adding to a basket does not reserve inventory. The checkout system must revalidate stock and prices.

### Attachments

The dialog accepts JPEG, PDF, Word (`.doc`, `.docx`), and Excel (`.xls`, `.xlsx`). Limits: three files per message, 5 MB per file, and 20 active files per shopper. Uploads remain private in PostgreSQL for 24 hours. Expired files must be uploaded again if their contents are needed in a later conversation turn.

Excel is parsed locally, including rows beyond the first 1,000, with limits of 5,000 rows, 100 columns, and 200,000 extracted characters. Oversized files are rejected explicitly. The agent reads large tables in 100-row portions and must identify any unprocessed rows. DOCX text and tables are extracted locally; embedded images require a PDF or JPEG. PDF, JPEG, and legacy DOC content is supplied to the configured OpenAI model, which must support those inputs. Uploaded file contents cannot authorize basket changes.

The integration follows the official [Responses file-input](https://developers.openai.com/api/docs/guides/file-inputs), [function-calling](https://developers.openai.com/api/docs/guides/function-calling), and [streaming](https://developers.openai.com/api/docs/guides/streaming-responses) contracts.

### Customer API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/product-chat/sessions` | Create a fixed product-consultant session. |
| `GET /api/product-chat/sessions/{id}` | Restore visible conversation, cards, proposals, and basket. |
| `POST /api/product-chat/sessions/{id}/messages` | `{ message, clientRequestId, attachmentIds?, productId? }`; request `Accept: text/event-stream` for `status`, `delta`, `completed`, and `error` events. Reuse the same request ID only when retrying the same message. |
| `POST /api/product-chat/sessions/{id}/attachments` | Multipart field `file`; returns an attachment ID. |
| `GET /api/basket` | Current basket and totals grouped by currency. |
| `POST /api/basket/proposals/{id}/confirm` | Explicitly confirm an addition, once. |
| `POST /api/basket/proposals/{id}/cancel` | Decline the proposal. |
| `POST /api/basket/proposals/{id}/refresh` | Revalidate and replace an expired/changed proposal; another explicit confirmation is required. |
| `PATCH /api/basket/items/{productId}` | `{ quantity, version }` from the basket UI. |
| `DELETE /api/basket/items/{productId}?version=…` | Remove an item. |

After a connection interruption, reload the conversation or basket before repeating an action. The product chat API is limited to 60 requests per minute per IP. Ordinary text requests should take seconds, but tool chains, model load, and attachments affect completion time; streaming reports progress during processing.

1. Start the API from the repository root with `dotnet run --project MemoryMunchers/MemoryMunchers --launch-profile http`.
2. In another terminal, serve this folder on port 5500 with `python -m http.server 5500 --directory frontend`.
3. Open `http://localhost:5500` in a browser.

## Product catalog UI

Open `http://localhost:5500/` to browse products. The previous `/products.html` address redirects to this default page. The page defaults to `http://localhost:5187`; expand **API connection** to change it. Run the API with PostgreSQL available using the instructions above. Database migrations run automatically when the API starts.

On opening or refreshing, the page checks the database against `MemoryMunchers/MemoryMunchers/nursultan_ekt_catalog.csv` (currently **12,853 unique products**). Missing products are imported automatically, then displayed with server-side pagination of **20, 50, or 100** rows. The expected count comes from the CSV, and matching IDs are checked too, so an equal count cannot hide missing products. Existing records are retained; unexpected IDs are reported as a count mismatch. Imports only add missing IDs, and concurrent or repeated imports do not create duplicates. An import is transactional, so a failure cannot leave a partially saved batch.

All CSV columns are stored, including multiline descriptions, category paths, JSON specifications, URLs, and nullable prices. The CSV is copied to build and publish output and is read from the API content root. Product prices reflect the supplied file, not live prices.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/products/status` | Expected/database/missing/unexpected counts, `isComplete`, and `requiresImport`. |
| `POST /api/products/import` | Add missing CSV products; return updated counts and `importedCount`. |
| `GET /api/products?page=1&pageSize=20` | Products ordered by ID, with `items`, `page`, `pageSize`, `totalCount`, and `totalPages`. Page sizes are limited to 20, 50, and 100; pages beyond the end return the last page. |
| `GET /api/products/{id}` | All saved fields for one product. |

## Agent chat UI

Open `http://localhost:5500/agents.html`, or choose **Agent chat** on the products page. The page uses the same static server as the catalog and defaults to the API's HTTP launch profile at `http://localhost:5187`. Expand **API connection** to change this address; it is remembered in your browser. For the HTTPS launch profile, use `https://localhost:7147` and trust the development certificate with `dotnet dev-certs https --trust`.

Choose an agent and start a conversation, optionally supplying a title and additional instructions. Send messages with Enter (Shift + Enter adds a new line), reopen saved conversations from the sidebar, and use **Load more** to see older sessions. Replies appear when the agent finishes; **Refresh chat** reloads saved messages and the latest run status.

The page calls `GET /api/agents`, `POST /api/agent-sessions`, `GET /api/agent-sessions?skip=…&take=…`, `GET /api/agent-sessions/{sessionId}`, and `POST /api/agent-sessions/{sessionId}/runs`. Run the API with its configured PostgreSQL database available and set the API key described below to receive agent replies.

## OpenAI agent access

The web API registers the agent workflow through `AddAgenticWorkflow` and calls the OpenAI Responses API. Create an API key using the [OpenAI quickstart](https://developers.openai.com/api/docs/quickstart), then set `OpenAI:ApiKey` in `MemoryMunchers/MemoryMunchers/appsettings.json`. Replace the empty `ApiKey` value in the existing `OpenAI` section, keeping its other settings:

```json
"ApiKey": "your-api-key-here"
```

Restart the API after setting the key. Keep the real key out of commits and frontend code. The `OpenAI__ApiKey` environment variable is still supported and overrides the value in `appsettings.json`.

If the key is empty, agent runs return HTTP 503 with `provider_not_configured` and setup instructions. A generic HTTP 500 response does not identify the cause; check the API's server logs for the underlying exception and the response's `traceId`.

Defaults in `MemoryMunchers/MemoryMunchers/appsettings.json` use [GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol) with its maximum output budget and reasoning effort:

| Setting | Default | Limit source |
| --- | --- | --- |
| `OpenAI:Model` | `gpt-6-sol` | OpenAI model ID |
| `OpenAI:MaxOutputTokens` | `128000` | Model maximum, including reasoning and visible output |
| `OpenAI:ReasoningEffort` | `max` | Highest supported reasoning effort |
| `OpenAI:RequestTimeoutSeconds` | `600` | Application maximum (10 minutes per request) |
| `Agents:RunTimeoutSeconds` | `1800` | Application maximum (30 minutes per run) |
| `Agents:Definitions:0:MaxModelCalls` | `32` | Application maximum per run |

Override these settings with environment variables using double underscores, such as `OpenAI__MaxOutputTokens` or `Agents__Definitions__0__MaxModelCalls`. The runner also permits up to 32 tool calls per run. The timeouts and call counts are application limits; API rate limits still depend on your OpenAI usage tier.

Create a session with `POST /api/agent-sessions`, then send messages to `POST /api/agent-sessions/{sessionId}/runs`. Sessions save their model and model-call limit when created, so create a new session to use the updated defaults. If overriding the model, configure output and reasoning settings supported by that model.

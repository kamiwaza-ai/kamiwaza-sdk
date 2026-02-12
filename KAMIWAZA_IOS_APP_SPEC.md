# Kamiwaza iOS App - Product Specification

## 1. Overview

A native iPhone application that connects to a self-hosted Kamiwaza AI Platform instance and provides a conversational chat interface with deployed language models. The app supports tool-augmented conversations, persistent device authentication, local chat history, and read-only browsing of platform resources.

**Platform**: iOS 16+
**Language**: Swift
**UI Framework**: SwiftUI
**Networking**: URLSession (with custom TLS trust evaluation)
**Local Storage**: SwiftData (or Core Data) for chat history and preferences; Keychain for credentials

---

## 2. First Launch & Server Configuration

### 2.1 Server Entry Screen

On the very first launch (or when no server is configured), the app presents a single-purpose screen:

- **Text field**: "Kamiwaza Server URL" (e.g. `https://kamiwaza.example.com`)
  - Accept both `http://` and `https://` schemes
  - If the user omits a scheme, default to `https://`
- **"Connect" button**: Initiates a server validation flow

### 2.2 Server Validation

When the user taps Connect:

1. Send `GET {base_url}/auth/health` (no authentication required)
2. **On success** (HTTP 200 with JSON body): Store the base URL in persistent app storage and proceed to the login screen
3. **On failure** (network error, non-200, non-JSON): Display an inline error: _"Could not reach a Kamiwaza server at this address. Please check the URL and try again."_
4. The user can later change the server URL from the Settings screen (gear icon)

### 2.3 Self-Signed Certificate Handling

When URLSession's `didReceive challenge` fires with a server trust challenge:

1. Evaluate the certificate chain
2. **If the leaf certificate is signed by a trusted CA**: Accept silently; no user interaction
3. **If self-signed or untrusted CA**:
   - Check local certificate store (Keychain or app-internal) for a previously pinned certificate for this host
   - **If pinned certificate matches**: Accept silently
   - **If pinned certificate does NOT match** (certificate changed): Present an alert:
     > "The security certificate for this server has changed since you last connected. This could indicate a security issue. Do you want to trust the new certificate?"
     >
     > [Cancel] [Trust New Certificate]
   - **If no pinned certificate exists** (first connection): Present an alert:
     > "The server at {host} is using a certificate not signed by a recognized certificate authority. This is common for private Kamiwaza installations. Do you want to trust this certificate?"
     >
     > [Cancel] [Trust Certificate]
   - On "Trust": Save the certificate's SHA-256 fingerprint + DER data to the app's local trust store, keyed by hostname. Accept the connection.
   - On "Cancel": Abort the connection; return to the server URL screen

**Implementation**: Use a custom `URLSessionDelegate` with `urlSession(_:didReceive:completionHandler:)`. Store pinned certificates in a dedicated Keychain item or an app-internal SQLite/SwiftData table keyed by hostname.

---

## 3. Authentication

### 3.1 Login Screen

After the server is validated, the app presents a login screen. The available login methods depend on the server's identity provider configuration.

**On load**, the app calls `GET {base_url}/auth/idp/public/providers` to discover which identity providers are enabled.

#### 3.1.1 Username & Password Login

Always available. The login form contains:

- **Username** text field
- **Password** secure text field
- **"Sign In" button**

On submit, the app calls:

```
POST {base_url}/auth/token
Content-Type: application/x-www-form-urlencoded

grant_type=password
&username={username}
&password={password}
&scope=openid email profile
&client_id=kamiwaza-platform
```

**Response** (`TokenResponse`):
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600,
  "refresh_token": "eyJ...",
  "id_token": "eyJ..."
}
```

On success, store the tokens in Keychain and proceed to the "Remember this device?" prompt.

On failure (401/403), display: _"Invalid username or password."_

#### 3.1.2 OAuth / Social Login

If the provider list includes an enabled provider (e.g., `alias: "google"`), render a button:

- **"Sign in with Google"** (styled per Google's branding guidelines)

On tap, initiate an OAuth Authorization Code flow using `ASWebAuthenticationSession`:

1. Open the Kamiwaza OAuth authorization URL for the provider (the specific URL format will be provided by the Kamiwaza server's OAuth configuration or well-known OIDC discovery)
2. Handle the redirect callback
3. Exchange the authorization code for tokens via the server
4. On success, store tokens in Keychain and proceed

Google is the first OAuth provider to integrate. The button should only appear when the `providers` list from `/auth/idp/public/providers` includes a Google provider with `enabled: true`.

Other providers should render with their `display_name` and a generic OAuth icon.

### 3.2 "Remember This Device?" Prompt

After successful login (any method), present a bottom-sheet or alert:

> "Remember this device?"
>
> "Stay signed in for 100 days without re-entering your credentials."
>
> [Not Now] [Remember]

**On "Remember"**:

1. Call `POST {base_url}/auth/pats` with:
   ```json
   {
     "name": "iOS App - {device_name}",
     "ttl_seconds": 8640000
   }
   ```
   (8,640,000 seconds = 100 days)

2. Response (`PATCreateResponse`):
   ```json
   {
     "token": "pat_eyJ...",
     "pat": {
       "id": "uuid",
       "jti": "unique-id",
       "owner_id": "user-id",
       "exp": 1234567890,
       "created_at": "...",
       "updated_at": "...",
       "revoked": false
     }
   }
   ```

3. Store the `token` value in Keychain as the primary credential
4. Discard the original access/refresh tokens (the PAT replaces them)
5. On all future app launches, use the PAT as a Bearer token: `Authorization: Bearer {pat_token}`

**On "Not Now"**:

- Keep the access token and refresh token
- Use standard token refresh flow (`POST /auth/refresh?refresh_token={refresh_token}`) when the access token expires
- On next app launch, if tokens are expired and cannot be refreshed, redirect to login

### 3.3 Token Refresh Logic

- Track `expires_in` from the token response
- Refresh proactively when the token has less than 30 seconds remaining
- If using a PAT: PATs are long-lived; no refresh needed until expiry. If a PAT returns 401, redirect to login
- If using access/refresh tokens: call `POST /auth/refresh?refresh_token={refresh_token}` to obtain new tokens

### 3.4 Logout

Accessible from the Settings screen (gear icon):

1. Call `POST {base_url}/auth/logout` with the current token
2. If a PAT was created for this device, call `DELETE /auth/pats/{jti}` to revoke it
3. Clear all Keychain credentials
4. Clear local preferences (but preserve chat history unless user explicitly deletes)
5. Return to the login screen

---

## 4. Main Chat Interface

### 4.1 Chat Home Screen

After login, the user lands on a screen with:

- A prominent **"Chat with Assistant"** button (large, centered, primary color)
- Below it, a scrollable list of previous chat sessions (if any), showing:
  - First message preview (truncated)
  - Timestamp
  - Model name used
- A tab bar or navigation providing access to: **Chat** | **Browse** | **Settings**

Tapping "Chat with Assistant" starts a new chat session. Tapping a previous chat opens it in read-only review mode (the user can continue the conversation from where they left off).

### 4.2 Model Auto-Selection

When starting a new chat, the app must determine which model to use:

1. Call `GET {base_url}/serving/deployments` to get all deployments
2. Filter to deployments where `status == "DEPLOYED"` and at least one instance has `status == "DEPLOYED"`
3. **If the user has chatted before**: Use the model from the user's most recent chat session (if that model is still deployed and active). If that deployment is no longer active, fall through to step 4.
4. **If the user has never chatted (or their last model is no longer deployed)**: Select the deployment with the **oldest `deployed_at` timestamp** among active deployments
5. If no models are deployed, show an informational screen: _"No models are currently deployed on your Kamiwaza server. Please deploy a model from the Kamiwaza web interface to start chatting."_

### 4.3 Model Indicator & Switcher

At the top of the chat screen, display a compact bar:

```
Model: Llama-3.1-8B-In...  [tap to change]
```

- The word **"Model:"** is styled as a tappable button (underlined or with a subtle chevron)
- The model name is truncated with ellipsis if too long
- **If only one model is deployed**: The "Model:" label is still visible but NOT tappable (appears dimmed/static). The full model name is still shown.
- **If multiple models are deployed**: Tapping "Model:" opens a bottom sheet listing all active deployments:
  - Each row shows: model name (`m_name`), deployment status indicator (green dot)
  - Tapping a model switches the current chat to use that model's deployment
  - The switch applies to new messages only; previous messages in the conversation remain associated with their original model

The model name displayed is the `m_name` field from `ActiveModelDeployment` or `UIModelDeployment`.

### 4.4 Chat Conversation UI

Standard chat interface:

- **Messages** displayed in a scrollable list:
  - User messages: right-aligned, colored bubble
  - Assistant messages: left-aligned, lighter bubble
  - Tool call indicators: inline, styled distinctly (see Section 5.4)
- **Text input** at the bottom with a send button
- **Keyboard** pushes the input field up
- Messages support basic markdown rendering (bold, italic, code blocks, lists)

### 4.5 Chat API Integration

The app sends chat completions via the OpenAI-compatible endpoint exposed by each deployment.

Each `ActiveModelDeployment` has an `endpoint` field containing the OpenAI-compatible base URL.

**Request** (OpenAI Chat Completions format):

```
POST {deployment.endpoint}/chat/completions
Authorization: Bearer {token}
Content-Type: application/json

{
  "model": "{model_name}",
  "messages": [
    {"role": "system", "content": "{system_prompt}"},
    {"role": "user", "content": "Hello!"}
  ],
  "temperature": 0.7,
  "stream": true
}
```

**Streaming**: The app should use SSE streaming (`"stream": true`) so tokens appear incrementally in the UI as they are generated. Parse the `data: {...}` lines from the SSE stream, extracting `choices[0].delta.content` and appending to the current assistant message.

**Non-streaming fallback**: If streaming fails, fall back to a non-streaming request (`"stream": false`) and display the complete response.

### 4.6 Chat History (Local Only)

All chat sessions are stored locally on-device using SwiftData (or Core Data):

**ChatSession entity**:
- `id`: UUID (generated locally)
- `createdAt`: Date
- `updatedAt`: Date
- `modelName`: String (the `m_name` from deployment)
- `deploymentId`: UUID
- `serverUrl`: String

**ChatMessage entity**:
- `id`: UUID
- `sessionId`: UUID (foreign key to ChatSession)
- `role`: String ("system" | "user" | "assistant" | "tool")
- `content`: String
- `timestamp`: Date
- `toolName`: String? (if role == "tool")
- `toolCallId`: String? (for tool call/response correlation)

Chat history is never sent to the server. It is purely for the user's local review and conversation continuity.

---

## 5. Tool Integration

### 5.1 Tool Discovery

When the user starts a new chat (or opens the tool selector), the app fetches available tools:

1. Call `GET {base_url}/tool/discover`
2. Response (`ToolDiscovery`):
   ```json
   {
     "servers": [
       {
         "deployment_id": "uuid",
         "name": "websearch",
         "url": "https://kamiwaza.example.com/tool/mcp/websearch",
         "status": "DEPLOYED",
         "capabilities": [
           {
             "name": "web_search",
             "description": "Search the web for information",
             "parameters": {
               "query": {"type": "string", "description": "Search query"},
               "max_results": {"type": "integer", "description": "Max results"}
             }
           }
         ],
         "created_at": "..."
       }
     ],
     "total": 1
   }
   ```
3. Only show tools where `status == "DEPLOYED"`

### 5.2 Tool Selection UI

When starting a new chat, if deployed tools exist, present a tool selection interface:

- Appears as a collapsible section above the chat input or as a sheet accessible via a tools icon
- Each tool shows:
  - Tool name
  - Tool description (from the first capability, or the server name)
  - A **checkbox** (toggle) to enable/disable
- At the bottom of the tool list: a **"Remember my tool selections"** checkbox

### 5.3 Remembering Tool Selections

If "Remember my tool selections" is checked:

- Persist the set of selected tool deployment IDs to local storage (UserDefaults or SwiftData)
- On starting any new chat in the future, automatically pre-select those tools
- If the user changes tool selections (checks or unchecks any tool) while "Remember" is active, the new selections overwrite the stored selections
- If the user unchecks "Remember my tool selections", stop auto-applying stored selections (but don't clear the stored data; re-checking will restore them)
- If a remembered tool is no longer deployed (not found in `/tool/discover`), silently skip it

### 5.4 Tool Calling Format (System Prompt Injection)

The app does NOT rely on native OpenAI-style `tools` parameter or `function_calling`. Instead, tools are injected into the system prompt using a generic tool-calling format.

**System prompt construction** when tools are enabled:

```
{user_system_prompt}

---

You have access to the following tools. To call a tool, respond with a tool call block in exactly this format:

<tool_call>
{"name": "tool_name", "arguments": {"param1": "value1", "param2": "value2"}}
</tool_call>

Available tools:

<tool>
{"name": "{capability.name}", "description": "{capability.description}", "parameters": {capability.parameters}}
</tool>

... (repeat for each enabled tool capability)

When you receive a tool result, it will be provided in this format:

<tool_result>
{"name": "tool_name", "result": ...}
</tool_result>

You may call multiple tools in sequence. After receiving tool results, continue your response to the user.
```

> **Note**: The exact format for `<tool_call>`, `<tool>`, and `<tool_result>` tags is a placeholder. The final format will be specified separately from existing Kamiwaza tool-calling code. The app architecture should make this format easily configurable (e.g., a single `ToolCallFormatter` protocol/class).

### 5.5 Tool Call Execution Flow

When the assistant's response contains a `<tool_call>` block:

1. **Parse** the tool call: extract `name` and `arguments`
2. **Display** an inline indicator in the chat:
   ```
   [wrench icon] Calling tool: web_search...
   ```
   The indicator should show a spinner while the tool executes.
3. **Execute** the tool call against the tool's MCP endpoint:
   - Look up the tool server URL from the discovered servers (match by capability name to server)
   - Send the tool call to the appropriate endpoint on the tool server URL
   - The exact MCP protocol call format will be specified later
4. **Receive** the tool result
5. **Display** the result status in the chat (collapse the spinner, show a checkmark):
   ```
   [checkmark icon] Tool result: web_search (tap to view)
   ```
   Tapping expands to show the raw result (for debugging/transparency).
6. **Inject** the tool result into the conversation as a new message with role "user" (containing the `<tool_result>` block) and immediately send the updated conversation back to the model for continuation
7. The model's next response is displayed as a normal assistant message
8. If the model calls another tool in its response, repeat from step 1

This entire flow (steps 1-7) should be **transparent to the user** except for the inline indicators. The user sees the model "thinking," a brief tool-calling animation, and then the final answer.

### 5.6 Tool Call Error Handling

- **Tool server unreachable**: Display inline: _"Tool {name} is unavailable. The assistant will continue without it."_ Inject an error tool result into the conversation so the model knows the tool failed.
- **Tool returns error**: Display the error inline and inject it as a tool result
- **Timeout** (30 seconds per tool call): Treat as an error

---

## 6. Settings Screen (Gear Icon)

Accessible from the main navigation via a gear icon in the top-right corner or tab bar.

### 6.1 Chat Parameters

#### System Prompt
- **Label**: "System Prompt"
- **Input**: Multi-line text field
- **Default value**: `"You are a helpful assistant."`
- Stored in UserDefaults. Applied to all new chat sessions. Changing does not affect existing chats.

#### Temperature
- **Label**: "Temperature"
- **Input**: Slider from 0.0 to 2.0 with 0.1 increments, plus a numeric text field for precise entry
- **Default value**: `0.7`
- Stored in UserDefaults. Applied to all new chat sessions.

### 6.2 Server Configuration

- **Current server**: Display the configured server URL
- **"Change Server"** button: Returns to the server entry screen
- Changing the server clears stored credentials and requires re-login (chat history is preserved but tagged with the old server URL)

### 6.3 Account

- **Current user**: Display username (from `GET /auth/users/me` cached at login)
- **"Logout"** button: Executes the logout flow (Section 3.4)

### 6.4 About

- App version
- SDK version / build number

---

## 7. Browse Screens (Read-Only)

The app provides read-only views for four resource types. These are informational only; the app does not create, modify, or delete these resources.

### 7.1 Models Browser

**Endpoint**: `GET {base_url}/models/`

Displays a list of all models known to the Kamiwaza instance:

| Field | Source |
|-------|--------|
| Name | `model.name` |
| Author | `model.author` |
| Family | `model.modelfamily` |
| Repository | `model.repo_modelId` |
| Quantizations | `model.available_quantizations` (badge list) |
| Files | `len(model.m_files)` file count |

Tapping a model opens a detail screen showing all fields, file list, and available quantizations.

### 7.2 Catalog Browser

**Endpoint**: `GET {base_url}/catalog/datasets/`

Displays datasets registered in the catalog:

| Field | Source |
|-------|--------|
| Name | `dataset.name` |
| Platform | `dataset.platform` |
| Environment | `dataset.environment` |
| Description | `dataset.description` |
| Tags | `dataset.tags` (badge list) |

Tapping a dataset shows full details including schema fields (from `GET /catalog/datasets/by-urn?urn={urn}` and `GET /catalog/datasets/by-urn/schema?urn={urn}`).

### 7.3 Apps Browser

**Endpoint**: `GET {base_url}/apps/deployments`

Displays deployed applications:

| Field | Source |
|-------|--------|
| Name | `deployment.name` |
| Status | `deployment.status` (color-coded badge) |
| Deployed At | `deployment.deployed_at` |
| Template | looked up via `deployment.template_id` from `/apps/app_templates` |

Tapping shows full deployment details.

Also display available app templates from `GET /apps/app_templates` in a separate section/tab.

### 7.4 Tools Browser

**Endpoint**: `GET {base_url}/tool/deployments`

Displays deployed tool servers:

| Field | Source |
|-------|--------|
| Name | `deployment.name` |
| Status | `deployment.status` (color-coded badge) |
| URL | `deployment.url` |
| Deployed At | `deployment.deployed_at` |

Tapping shows deployment details and capabilities (from `/tool/discover` matched by deployment ID).

Also display available tool templates from `GET /tool/templates` in a separate section.

---

## 8. Navigation Structure

```
App Launch
  |
  v
[Server URL Entry] (first launch only)
  |
  v
[Login Screen]
  |-- Username/Password
  |-- OAuth (Google, etc.)
  |
  v
[Remember Device? prompt]
  |
  v
[Main Tab Bar]
  |
  |-- Tab 1: Chat
  |     |-- Chat Home (history list + "Chat with Assistant" button)
  |     |-- Chat Session (conversation UI)
  |     |     |-- Model Indicator (top)
  |     |     |-- Model Switcher (bottom sheet)
  |     |     |-- Tool Selector (collapsible/sheet)
  |     |     |-- Message List
  |     |     |-- Text Input
  |
  |-- Tab 2: Browse
  |     |-- Segmented control or sub-tabs: Models | Catalog | Apps | Tools
  |     |-- List view for each
  |     |-- Detail view on tap
  |
  |-- Tab 3: Settings (gear icon)
        |-- System Prompt
        |-- Temperature
        |-- Server Configuration
        |-- Account / Logout
        |-- About
```

---

## 9. API Reference Summary

All API calls use the base URL configured in Section 2. All authenticated calls include `Authorization: Bearer {token}` header.

### Authentication

| Action | Method | Path | Auth | Notes |
|--------|--------|------|------|-------|
| Server health check | GET | `/auth/health` | No | Validates server is reachable |
| List public IdPs | GET | `/auth/idp/public/providers` | No | Discover OAuth providers |
| Login (password) | POST | `/auth/token` | No | Form-encoded, password grant |
| Refresh token | POST | `/auth/refresh?refresh_token={rt}` | No | Exchange refresh token |
| Get current user | GET | `/auth/users/me` | Yes | Returns username, email, roles |
| Create PAT | POST | `/auth/pats` | Yes | `ttl_seconds=8640000` for 100-day |
| Revoke PAT | DELETE | `/auth/pats/{jti}` | Yes | On logout if PAT was created |
| Logout | POST | `/auth/logout` | Yes | Server-side session cleanup |

### Model Deployments

| Action | Method | Path | Auth | Notes |
|--------|--------|------|------|-------|
| List deployments | GET | `/serving/deployments` | Yes | Returns `UIModelDeployment[]` |
| List active deployments | GET | `/serving/active_deployments` | Yes | Returns `ActiveModelDeployment[]` with `endpoint` |
| Get deployment | GET | `/serving/deployment/{id}` | Yes | Single deployment details |

### Chat Completions

| Action | Method | Path | Auth | Notes |
|--------|--------|------|------|-------|
| Chat completion | POST | `{deployment.endpoint}/chat/completions` | Yes | OpenAI-compatible format |

### Tools

| Action | Method | Path | Auth | Notes |
|--------|--------|------|------|-------|
| Discover tool servers | GET | `/tool/discover` | Yes | Returns servers + capabilities |
| List tool deployments | GET | `/tool/deployments` | Yes | Returns `ToolDeployment[]` |
| List tool templates | GET | `/tool/templates` | Yes | Imported templates |

### Models (Browse)

| Action | Method | Path | Auth | Notes |
|--------|--------|------|------|-------|
| List models | GET | `/models/` | Yes | Returns `Model[]` |
| Get model | GET | `/models/{id}` | Yes | Single model details |

### Catalog (Browse)

| Action | Method | Path | Auth | Notes |
|--------|--------|------|------|-------|
| List datasets | GET | `/catalog/datasets/` | Yes | Returns `Dataset[]` |
| Get dataset | GET | `/catalog/datasets/by-urn?urn={urn}` | Yes | Single dataset |
| Get dataset schema | GET | `/catalog/datasets/by-urn/schema?urn={urn}` | Yes | Field definitions |

### Apps (Browse)

| Action | Method | Path | Auth | Notes |
|--------|--------|------|------|-------|
| List app deployments | GET | `/apps/deployments` | Yes | Returns `AppDeployment[]` |
| List app templates | GET | `/apps/app_templates` | Yes | Returns `AppTemplate[]` |

---

## 10. Data Models (Swift)

Below are the primary Swift structs the app should define, mirroring the relevant Kamiwaza API schemas.

```swift
// MARK: - Authentication

struct TokenResponse: Codable {
    let accessToken: String
    let tokenType: String
    let expiresIn: Int
    let refreshToken: String?
    let idToken: String?
}

struct UserInfo: Codable {
    let username: String
    let email: String?
    let groups: [String]
    let roles: [String]
    let sub: String
}

struct PATCreateRequest: Codable {
    let name: String?
    let ttlSeconds: Int?
}

struct PATCreateResponse: Codable {
    let token: String
    let pat: PAT
}

struct PAT: Codable {
    let id: UUID
    let jti: String
    let ownerId: String
    let exp: Int?
    let createdAt: String
    let updatedAt: String
    let revoked: Bool
}

struct IdentityProvider: Codable {
    let alias: String
    let providerId: String?
    let displayName: String?
    let enabled: Bool
}

struct IdentityProviderListResponse: Codable {
    let providers: [IdentityProvider]
    let idpManagementEnabled: Bool
}

// MARK: - Deployments

struct ActiveModelDeployment: Codable {
    let id: UUID
    let mId: UUID
    let mName: String
    let status: String
    let instances: [ModelInstance]
    let lbPort: Int
    let endpoint: String?
}

struct UIModelDeployment: Codable {
    let id: UUID
    let mId: UUID
    let mName: String?
    let status: String
    let requestedAt: String
    let deployedAt: String?
    let instances: [ModelInstance]
}

struct ModelInstance: Codable {
    let id: UUID
    let deploymentId: UUID
    let deployedAt: String
    let containerId: String?
    let hostName: String?
    let listenPort: Int?
    let status: String?
}

// MARK: - Models (Browse)

struct Model: Codable {
    let id: UUID?
    let name: String
    let repoModelId: String?
    let modelfamily: String?
    let author: String?
    let version: String?
    let hub: String?
    let description: String?
    let availableQuantizations: [String]
    let createdTimestamp: String?
}

// MARK: - Tools

struct ToolDiscovery: Codable {
    let servers: [ToolServerInfo]
    let total: Int
}

struct ToolServerInfo: Codable {
    let deploymentId: UUID
    let name: String
    let url: String
    let status: String
    let capabilities: [ToolCapability]?
    let createdAt: String
}

struct ToolCapability: Codable {
    let name: String
    let description: String?
    let parameters: [String: AnyCodable]?  // Use AnyCodable or JSONValue wrapper
}

struct ToolDeployment: Codable {
    let id: UUID
    let name: String
    let status: String
    let url: String
    let deployedAt: String?
    let createdAt: String
}

// MARK: - Catalog (Browse)

struct Dataset: Codable {
    let urn: String
    let name: String
    let platform: String
    let environment: String
    let description: String?
    let tags: [String]
}

// MARK: - Apps (Browse)

struct AppDeployment: Codable {
    let id: UUID
    let name: String
    let templateId: UUID?
    let status: String
    let deployedAt: String?
    let createdAt: String
}

struct AppTemplate: Codable {
    let id: UUID
    let name: String
    let version: String?
    let description: String?
    let riskTier: Int
    let verified: Bool
}

// MARK: - Local Storage

struct ChatSession {  // SwiftData @Model
    let id: UUID
    var createdAt: Date
    var updatedAt: Date
    var modelName: String
    var deploymentId: UUID
    var serverUrl: String
    var messages: [ChatMessage]
}

struct ChatMessage {  // SwiftData @Model
    let id: UUID
    var role: String       // "system", "user", "assistant", "tool"
    var content: String
    var timestamp: Date
    var toolName: String?
    var toolCallId: String?
}
```

> **Note**: Use `CodingKeys` with `convertFromSnakeCase` strategy or explicit key mappings to bridge between the API's snake_case JSON and Swift's camelCase conventions.

---

## 11. Networking Layer

### 11.1 API Client Architecture

Create a `KamiwazaAPIClient` class that encapsulates:

- Base URL storage
- Token management (PAT or access+refresh tokens)
- Automatic `Authorization: Bearer` header injection
- Automatic token refresh on 401 responses (retry the failed request once after refresh)
- Custom `URLSessionDelegate` for certificate pinning (Section 2.3)
- JSON decoding with snake_case key strategy

### 11.2 SSL/TLS Configuration

```swift
class KamiwazaSessionDelegate: NSObject, URLSessionDelegate {
    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let serverTrust = challenge.protectionSpace.serverTrust else {
            completionHandler(.performDefaultHandling, nil)
            return
        }

        // 1. Try system trust evaluation first
        // 2. If fails, check local pinned certificate store
        // 3. If no pin or pin mismatch, prompt user (via delegate callback)
        // 4. If user accepts, pin and accept
    }
}
```

### 11.3 Request/Response Pattern

All API calls should go through a central method:

```swift
func request<T: Decodable>(
    method: HTTPMethod,
    path: String,
    body: Encodable? = nil,
    queryParams: [String: String]? = nil,
    skipAuth: Bool = false
) async throws -> T
```

---

## 12. Error Handling

### 12.1 Network Errors

- **No internet**: Show a banner at the top of the screen: _"No internet connection"_
- **Server unreachable**: _"Cannot reach your Kamiwaza server. Check your connection and server status."_
- **Timeout** (30s default): _"Request timed out. Please try again."_

### 12.2 Authentication Errors

- **401 with refresh token**: Attempt silent refresh. If refresh fails, redirect to login.
- **401 with PAT**: PAT may be expired or revoked. Redirect to login.
- **403**: _"You don't have permission to perform this action."_

### 12.3 API Errors

- **404**: _"Resource not found."_ (handle gracefully in browse screens)
- **422**: Validation error; display the error message from the response body
- **500**: _"Server error. Please try again or contact your administrator."_

### 12.4 Chat Errors

- **Streaming connection lost**: Show inline: _"Connection lost. Tap to retry."_ The retry resends the last user message.
- **Model deployment became unavailable mid-chat**: _"The model is no longer available. Please select a different model."_ Activate the model switcher.

---

## 13. Security Considerations

1. **Keychain storage**: All tokens (PAT, access token, refresh token) must be stored in the iOS Keychain with `kSecAttrAccessibleWhenUnlockedThisDeviceOnly` protection level
2. **No credential logging**: Never log tokens, passwords, or PAT values
3. **Certificate pinning**: Pinned certificate fingerprints stored locally; validated on every connection
4. **Input sanitization**: Sanitize user input before injecting into system prompts (prevent prompt injection via tool names/descriptions is a server-side concern, but the app should not execute arbitrary code from tool results)
5. **Local chat storage**: Chat history is on-device only; no server sync. Consider offering an option to require device passcode/biometrics to view chat history in a future version
6. **Clipboard**: Do not automatically copy tokens to clipboard

---

## 14. Future Considerations (Out of Scope for MVP)

The following are explicitly **not** included in the MVP but should be considered in the architecture to avoid costly refactors:

- **Push notifications** for long-running operations
- **iPad / macOS Catalyst** support
- **Voice input** (speech-to-text for chat)
- **Image/file attachments** in chat
- **Multiple server profiles** (connecting to different Kamiwaza instances)
- **Biometric lock** for the app
- **Chat search** across history
- **Chat export** (share/email conversations)
- **Offline mode** (queue messages for when connectivity returns)
- **Widget** showing deployment status
- **App deployment management** (start/stop apps from the mobile app)
- **Dark mode / theme customization** (use system default for MVP)

---

## 15. Glossary

| Term | Definition |
|------|-----------|
| **Kamiwaza** | Self-hosted AI platform that manages model deployment, tools, and data |
| **Deployment** | A running instance of an AI model, served via an OpenAI-compatible endpoint |
| **PAT** | Personal Access Token; a long-lived bearer token for API authentication |
| **MCP** | Model Context Protocol; the protocol used by Kamiwaza tool servers |
| **Tool** | An external capability (web search, database query, etc.) deployed as an MCP server |
| **Catalog** | Kamiwaza's data catalog for managing datasets, containers, and secrets |
| **App** | A containerized application deployed via Kamiwaza's App Garden |
| **Active Deployment** | A model deployment with status "DEPLOYED" and at least one running instance |

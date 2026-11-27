import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";
import type { BundleManifest, Labels, LooseRecord, RunProcess } from "../../../shared/types.js";
import { certificationEvidenceUrl } from "../../../shared/command-certification.js";
import { evaluatePrecheck, evaluateSetupInstallSizePrecheck, fileStateValues, runDataSource } from "./action-runner.js";
import { serveBundleFavicon, serveBundleFile } from "./assets.js";
import { loadLocaleOptions } from "./bundle-loader.js";
import { saveBundleState } from "./bundle-state.js";
import { readNetworkSettings, requireNetworkChoice, saveNetworkSettings } from "./network-settings.js";
import { loadConfig, saveConfig } from "./config-store.js";
import { errorMessage, HttpError } from "./errors.js";
import { contentType, json, notFound, readJSONBody, serveIndex, staticFile } from "./http.js";
import { findConfigEditorControl, locationKey, resolveActionReference, resolveDataSource, type DynamicActions } from "./manifest-lookup.js";
import { streamNDJSON, type EmitEvent } from "./ndjson.js";
import { JobSubmissionUnknown, runActionPreferringServer } from "./job-server-action.js";
import type { ActionEvent } from "./action-runner.js";
import { lastResultDocumentPaths, rememberLastResultDocument } from "./last-result-documents.js";
import { openResultPath, ResultPaths, systemOpener } from "./result-paths.js";
import { readResultCard } from "./result-card.js";
import { installPrerequisite, missingPrerequisites } from "./prerequisites.js";
import { actionWithForce, findExistingOutput } from "./existing-output.js";
import { multiPathControlsFromManifest } from "../../../shared/rendering.js";
import { resultDocumentSpecFor } from "../../../shared/result-document.js";
import type { JobServer } from "./job-server.js";
import { appSupportDirectory, configPath, distModulePath, normalizeContext } from "./paths.js";
import { pickPath, type NativePickerEndpoint } from "./path-picker.js";
import { optionalString, parseActionReference, parseBundleStatePatch, parseCommandContext, parseLabels, parseManifestLocation, parsePrecheckTarget, requestObject, requiredString } from "./request-bodies.js";
import { applySecurityHeaders, contentSecurityPolicy, guardRequest } from "./request-guard.js";
import { runSetup, runUninstall } from "./setup-runner.js";
import { wrapEmitWithEventLog } from "./setup-event-log.js";
import { bundleArchiveFormat } from "./bundle-archive.js";
import { assertCurrentUsersWorkspace, usersWorkspaceFor, usersWorkspaceLocationInfo, workspaceMoveRevision } from "./users-workspace.js";
import { withWorkspaceMove, withWorkspaceUse } from "./workspace-activity.js";
import type { WorkspaceMoveTarget } from "./workspace-move.js";
import { LinkedMoveFolderError } from "./workspace-move-guard.js";
import { requireViewerLoopback, ViewerFiles } from "./viewer-files.js";
import { checkLibraryTracks, openLibraryViewer, viewerLibraries } from "./viewer-session.js";
import { isViewerBuild } from "../../../shared/viewer-rows.js";
import { lookupViewerRsid } from "./viewer-rsids.js";
import { RemoteViewerConnection } from "./remote-viewer-connection.js";
import { RemoteViewer } from "./remote-viewer.js";
import { openGeneIobio } from "./gene-iobio.js";
import { openSystemBrowser } from "./system-browser.js";
import { genePagePrerequisites, installGenePagePrerequisite, openGenePage } from "./gene-page.js";

type ManifestResponse = Record<string, unknown> & { appVersion?: string; autoRunSetup?: boolean };

const networkChoiceRoutes = new Set([
    "/api/bundle/load", "/api/viewer/remote/open", "/api/gene/iobio/open", "/api/certification/open",
    "/api/gene/prerequisites/install/stream", "/api/prerequisites/install/stream",
    "/api/run/stream", "/api/datasource", "/api/precheck",
]);

interface LocalizedBundle {
    manifest: BundleManifest;
    labels?: Labels;
    [key: string]: unknown;
}

export interface RequestHandlerContext {
    addDevReloadClient: (response: ServerResponse) => void;
    openBrowser?: (url: URL) => Promise<void>;
    apiToken: string;
    /** True when a launcher (Tauri/Electron) injects the token into its own webview instead of index.html. */
    apiTokenInjectedByLauncher?: boolean;
    appVersion?: string;
    bundleRoot: string;
    defaultLocale?: string;
    distRoot: string;
    /** Actions most recently returned by control data sources, which replace the control's declared row actions. */
    dynamicActions: DynamicActions;
    enableDevReload: boolean;
    hostnames?: string[];
    /** The bundle's job server, when its manifest declares one. */
    jobServer: JobServer | undefined;
    localizedBundleLoader: {
        load: (locale?: string, preferredLocales?: string[]) => Promise<LocalizedBundle>;
        /** The startup preload, which holds a workspace use while it bootstraps the settings files. */
        preloaded?: Promise<unknown> | undefined;
    };
    loadBundle: (requestedSource: string) => Promise<LooseRecord>;
    /** Moves the users workspace, or one setting's folder, and loads the bundle again. */
    moveWorkspace: (target: WorkspaceMoveTarget, emit: EmitEvent, signal: AbortSignal) => Promise<void>;
    /** The workspace manifest; actions, data sources, and settings files are only ever resolved from it. */
    manifest: BundleManifest;
    maxBodyBytes: number;
    nativePicker?: NativePickerEndpoint | undefined;
    /** Backend-only credential storage override for embedding hosts and tests. */
    remoteViewerConfigFile?: string;
    repoRoot: string;
    runProcess: RunProcess;
    shutdown: (reason: string) => void;
    sourceBundleRoot: string;
    webRoot: string;
    workspaceMoveRevision?: string;
}

type RouteHandler = (response: ServerResponse, headOnly: boolean) => Promise<void>;
type JSONHandler = (body: LooseRecord) => Promise<unknown>;
type StepRunner = typeof runSetup;

const maxRememberedPicks = 16;
const independentlyGuardedRoutes = new Set([
    "/api/workspace-location/move", "/api/setup/stream", "/api/uninstall/stream",
    "/api/path/pick", "/api/shutdown", "/api/gene/iobio/open", "/api/certification/open",
]);

async function assertCurrentWorkspace(context: RequestHandlerContext, request?: IncomingMessage) {
    await assertCurrentUsersWorkspace(context.manifest, context.bundleRoot);
    const revision = await workspaceMoveRevision(context.manifest);
    const client = request?.headers["x-wgse-workspace-revision"];
    if ((context.workspaceMoveRevision !== undefined && context.workspaceMoveRevision !== revision) ||
        (client !== undefined && client !== revision)) {
        throw new HttpError(409, "workspaceMoveStale");
    }
    return revision;
}

function withCurrentWorkspace<T>(context: RequestHandlerContext, run: () => Promise<T>, request?: IncomingMessage) {
    return withWorkspaceUse(async () => {
        await assertCurrentWorkspace(context, request);
        return run();
    });
}

export function createRequestHandler(context: RequestHandlerContext) {
    // Bundles can only be loaded from folders the user picked in a native dialog during this session.
    const pickedBundlePaths = new Set<string>();
    const resultPaths = new ResultPaths();
    const viewerFiles = new ViewerFiles();
    const remoteViewer = new RemoteViewer(new RemoteViewerConnection(
        context.remoteViewerConfigFile ?? path.join(appSupportDirectory(), "RemoteViewer", "config.toml"),
    ));
    const requireRemoteViewer = () => {
        if (context.manifest.id !== "wgs-extract") throw new HttpError(403, "remoteUnavailable");
    };
    const staticRoutes: Record<string, RouteHandler> = {
        "/": (response, headOnly) => serveIndex(path.join(context.webRoot, "index.html"), indexToken(context), response, headOnly),
        "/index.html": (response, headOnly) => serveIndex(path.join(context.webRoot, "index.html"), indexToken(context), response, headOnly),
        "/favicon.ico": (response, headOnly) => serveBundleFavicon(response, headOnly, context.bundleRoot),
        "/client/app.js": (response, headOnly) => staticFile(path.join(context.distRoot, "web", "src", "client", "app.js"), "text/javascript; charset=utf-8", response, headOnly),
        "/styles.css": (response, headOnly) => staticFile(path.join(context.webRoot, "styles.css"), "text/css; charset=utf-8", response, headOnly),
        "/viewer": (response, headOnly) => {
            // CRAM's bundled decoder uses WASM; network access stays same-origin only.
            response.setHeader("content-security-policy", contentSecurityPolicy
                .replace("script-src 'self'", "script-src 'self' 'wasm-unsafe-eval'")
                .replace("frame-ancestors 'none'", "frame-ancestors 'self'")
                .replace(/connect-src[^;]+/, "connect-src 'self'"));
            return serveIndex(path.join(context.distRoot, "web", "viewer.html"), indexToken(context), response, headOnly);
        },
        "/viewer.css": (response, headOnly) => staticFile(path.join(context.distRoot, "web", "viewer.css"), "text/css; charset=utf-8", response, headOnly),
    };
    const postRoutes: Record<string, (request: IncomingMessage, response: ServerResponse) => Promise<void>> = {
        "/api/viewer/open": async (request, response) => {
            requireViewerLoopback(request);
            const body = await readBody(request, context);
            const tracks = body.tracks ?? [];
            if (!Array.isArray(tracks) || tracks.some((track) => typeof track !== "string" || !track.trim() || track.length > 4096)) {
                throw new HttpError(400, "Viewer tracks must be local file paths.");
            }
            if (tracks.length > 16) {
                json(response, { problem: "trackLimit" });
                return;
            }
            const build = body.build;
            if (build !== undefined && !isViewerBuild(build)) {
                throw new HttpError(400, "Viewer build must be hg19 or hg38.");
            }
            json(response, await openLibraryViewer(requiredString(body, "path"), optionalString(body, "locus") ?? "", context, viewerFiles, tracks, build));
        },
        "/api/viewer/check": async (request, response) => {
            requireViewerLoopback(request);
            const body = await readBody(request, context);
            const paths = body.paths;
            if (!Array.isArray(paths) || paths.length > 16 || paths.some((file) => typeof file !== "string" || !file.trim() || file.length > 4096)) {
                throw new HttpError(400, "Viewer checks take up to 16 local file paths.");
            }
            json(response, { tracks: await checkLibraryTracks(paths, context) });
        },
        "/api/viewer/close": jsonRoute(context, async (body) => {
            viewerFiles.revoke(requiredString(body, "id"));
            remoteViewer.revoke(requiredString(body, "id"));
            return { ok: true };
        }),
        "/api/viewer/rsid": async (request, response) => {
            requireViewerLoopback(request);
            const body = await readBody(request, context);
            const id = requiredString(body, "id");
            const rsid = requiredString(body, "rsid");
            const library = await viewerLibraries(context);
            const index = viewerFiles.rsidIndexFor(id, library.key);
            if (!index) {
                json(response, { problem: "rsidSearchUnavailable" });
                return;
            }
            json(response, await lookupViewerRsid(index, rsid, library.referenceRoots));
        },
        "/api/viewer/remote/status": jsonRoute(context, async () => {
            requireRemoteViewer();
            return remoteViewer.connection.status();
        }),
        "/api/viewer/remote/import": async (request, response) => {
            requireRemoteViewer();
            requireViewerLoopback(request);
            const body = await readBody(request, context);
            const owner = requiredString(body, "requestID");
            const generation = remoteViewer.beginImport(owner);
            const close = () => {
                if (!response.writableFinished) remoteViewer.connection.cancelImport(owner);
            };
            response.once("close", close);
            try {
                if (response.destroyed) throw new HttpError(409, "remoteConnectionChanged");
                const picked = await pickPath({ kind: "file", title: requiredString(body, "title"), nativePicker: context.nativePicker });
                if (picked.cancelled) {
                    json(response, await remoteViewer.connection.status());
                    return;
                }
                const status = await remoteViewer.connection.import(picked.path, generation);
                json(response, status);
            } finally {
                response.off("close", close);
                remoteViewer.connection.finishImport(owner);
            }
        },
        "/api/viewer/remote/forget": jsonRoute(context, async () => {
            requireRemoteViewer();
            remoteViewer.revokeAll();
            await remoteViewer.connection.forget();
            return {};
        }),
        "/api/viewer/remote/open": async (request, response) => {
            requireRemoteViewer();
            requireViewerLoopback(request);
            const body = await readBody(request, context);
            const controller = new AbortController();
            const close = () => {
                if (!response.writableFinished) controller.abort();
            };
            response.once("close", close);
            if (response.destroyed) controller.abort();
            try {
                json(response, await remoteViewer.open(requiredString(body, "requestID"), requiredString(body, "id"),
                    requiredString(body, "path"), requiredString(body, "index"), requiredString(body, "reference"),
                    requiredString(body, "locus"), body.confirmed === true, controller.signal));
            } finally {
                response.off("close", close);
            }
        },
        "/api/gene/open": async (request, response) => {
            // It runs the CLI on local paths, so only this computer may ask, as with the viewer.
            requireViewerLoopback(request);
            const body = await readBody(request, context);
            const controller = new AbortController();
            const close = () => {
                if (!response.writableFinished) controller.abort();
            };
            response.once("close", close);
            try {
                json(response, await openGenePage(body, context, resultPaths, controller.signal));
            } finally {
                response.off("close", close);
            }
        },
        "/api/gene/iobio/open": async (request, response) => {
            requireViewerLoopback(request);
            json(response, await openGeneIobio(await readBody(request, context), context.openBrowser));
        },
        "/api/certification/open": async (request, response) => {
            requireViewerLoopback(request);
            const url = certificationEvidenceUrl(requiredString(await readBody(request, context), "command"));
            if (!url) throw new HttpError(404, "Unknown certification command.");
            await (context.openBrowser ?? openSystemBrowser)(url);
            json(response, { ok: true });
        },
        "/api/gene/prerequisites": async (request, response) => {
            requireViewerLoopback(request);
            const body = await readBody(request, context);
            json(response, await genePagePrerequisites(body, context));
        },
        "/api/gene/prerequisites/install/stream": async (request, response) => {
            requireViewerLoopback(request);
            const body = await readBody(request, context);
            const id = requiredString(body, "id");
            await streamNDJSON(
                response,
                (emit, signal) => installGenePagePrerequisite(
                    body, id, context, signal,
                    (event: ActionEvent) => {
                        if (event.type === "complete") {
                            resultPaths.remember(event.result.resultDocument, context.bundleRoot);
                        }
                        return emit(event);
                    },
                    (installAction) => resultDocumentSpecFor(context.manifest, installAction),
                ),
                (error) => ({ type: "error", error }),
            );
        },
        "/api/datasource": jsonRoute(context, async (body) => {
            const location = parseManifestLocation(body.source);
            const payload = await runDataSource(resolveDataSource(context.manifest, location), commandContext(body, context), context.bundleRoot, context.runProcess);
            rememberDynamicActions(context, location, payload);
            return payload;
        }),
        "/api/existing-output": jsonRoute(context, async (body) => {
            const action = resolveActionReference(context.manifest, parseActionReference(body.action), context.dynamicActions);
            return findExistingOutput(context.manifest, action, commandContext(body, context), context.bundleRoot, resultPaths);
        }),
        "/api/run/stream": async (request, response) => {
            const body = await readBody(request, context);
            const resolvedAction = resolveActionReference(context.manifest, parseActionReference(body.action), context.dynamicActions);
            const { bundleRoot } = context;
            const actionContext = { ...commandContext(body, context), lastResultDocumentPaths: await lastResultDocumentPaths(bundleRoot) };
            const action = body.force === true ? await actionWithForce(resolvedAction, actionContext, bundleRoot) : resolvedAction;
            const resultSpec = resultDocumentSpecFor(context.manifest, action);
            await streamNDJSON(
                response,
                (emit, signal) => runActionPreferringServer(action, actionContext, signal, bundleRoot, context.runProcess, context.jobServer, async (event: ActionEvent) => {
                    if (event.type !== "complete") {
                        return emit(event);
                    }
                    resultPaths.remember(event.result.resultDocument, bundleRoot);
                    await rememberLastResultDocument(bundleRoot, action, event.result);
                    return emit({ ...event, result: { ...event.result, lastResultDocumentPaths: await lastResultDocumentPaths(bundleRoot) } });
                }, resultSpec),
                (error, cause) => cause instanceof JobSubmissionUnknown
                    ? { type: "submission-unknown", jobID: cause.jobID }
                    : { type: "error", error },
            );
        },
        "/api/result/open": jsonRoute(context, async (body) => {
            await openResultPath(requiredString(body, "path"), body.reveal === true, resultPaths, context.runProcess, context.bundleRoot);
            return { ok: true };
        }),
        "/api/result/card": jsonRoute(context, async (body) => readResultCard(requiredString(body, "path"), resultPaths, context.bundleRoot)),
        "/api/precheck": jsonRoute(context, async (body) => {
            const target = parsePrecheckTarget(body.target);
            const labels = parseLabels(body.labels);
            if (target.kind === "setup") {
                return evaluateSetupInstallSizePrecheck(context.manifest, labels, context.bundleRoot, context.runProcess);
            }
            const action = resolveActionReference(context.manifest, target, context.dynamicActions);
            return evaluatePrecheck(action.precheck, commandContext(body, context), labels, context.bundleRoot, context.runProcess);
        }),
        "/api/prerequisites": jsonRoute(context, async (body) => {
            const action = resolveActionReference(context.manifest, parseActionReference(body.action), context.dynamicActions);
            return { missing: await missingPrerequisites(context.manifest, action, commandContext(body, context), context.bundleRoot, context.runProcess) };
        }),
        "/api/prerequisites/install/stream": async (request, response) => {
            const body = await readBody(request, context);
            const action = resolveActionReference(context.manifest, parseActionReference(body.action), context.dynamicActions);
            const id = requiredString(body, "id");
            await streamNDJSON(
                response,
                (emit, signal) => installPrerequisite(
                    context.manifest, action, id, commandContext(body, context), signal, context.bundleRoot, context.runProcess,
                    (event: ActionEvent) => {
                        if (event.type === "complete") {
                            resultPaths.remember(event.result.resultDocument, context.bundleRoot);
                        }
                        return emit(event);
                    },
                    (installAction) => resultDocumentSpecFor(context.manifest, installAction),
                ),
                (error) => ({ type: "error", error }),
            );
        },
        "/api/file-state": jsonRoute(context, async (body) => ({ values: await fileStateValues(commandContext(body, context), context.bundleRoot) })),
        "/api/setup/stream": (request, response) => handleStepStream(request, response, context, runSetup),
        "/api/uninstall/stream": (request, response) => handleStepStream(request, response, context, runUninstall),
        "/api/path/pick": jsonRoute(context, async (body) => {
            const result = await pickPath({ kind: body.kind, title: body.title, defaultPath: body.defaultPath, multiple: body.multiple, bundleRoot: context.bundleRoot, nativePicker: context.nativePicker });
            if (!result.cancelled && (result.kind === "directory" || path.basename(result.path).toLowerCase() === "manifest.json" || bundleArchiveFormat(result.path) != null)) {
                pickedBundlePaths.add(result.path);
                for (const stale of [...pickedBundlePaths].slice(0, -maxRememberedPicks)) {
                    pickedBundlePaths.delete(stale);
                }
            }
            return result;
        }),
        "/api/bundle/load": jsonRoute(context, async (body) => {
            const requested = requiredString(body, "path");
            if (!pickedBundlePaths.delete(requested)) {
                throw new HttpError(403, "Bundles can only be loaded from a path chosen in the file dialog.");
            }
            return context.loadBundle(requested);
        }),
        "/api/shutdown": jsonRoute(context, async () => {
            setTimeout(() => context.shutdown("apiShutdown"), 0).unref();
            return { ok: true };
        }),
        "/api/open-bundle-workspace": jsonRoute(context, async () => {
            await openPath(context.bundleRoot, context.runProcess);
            return { ok: true };
        }),
        "/api/workspace-location/move": async (request, response) => {
            const body = await readBody(request, context);
            const folder = optionalString(body, "folder")?.trim() || null;
            const controlID = optionalString(body, "controlID");
            const key = optionalString(body, "key");
            const target: WorkspaceMoveTarget = { folder, ...(controlID && key ? { setting: { controlID, key } } : {}) };
            // The app's own startup preload is not a running command; wait for it instead of refusing the move as busy.
            await context.localizedBundleLoader.preloaded?.catch(() => undefined);
            await streamNDJSON(
                response,
                (emit, signal) => withWorkspaceMove(async () => {
                    await assertCurrentWorkspace(context, request);
                    await context.moveWorkspace(target, emit, signal);
                    context.workspaceMoveRevision = await workspaceMoveRevision(context.manifest);
                    await emit({ type: "complete" });
                }),
                (error, cause) => ({
                    type: "error",
                    error,
                    ...(cause instanceof LinkedMoveFolderError ? { linkedFolder: { link: cause.link, target: cause.target } } : {}),
                }),
            );
        },
        "/api/config/load": jsonRoute(context, async (body) => {
            const control = findConfigEditorControl(context.manifest, requiredString(body, "controlID"));
            return loadConfig(control, optionalString(body, "path"), context.bundleRoot);
        }),
        "/api/config/save": jsonRoute(context, async (body) => {
            const control = findConfigEditorControl(context.manifest, requiredString(body, "controlID"));
            return saveConfig(control, optionalString(body, "path"), requestObject(body.values ?? {}, "values"), context.bundleRoot);
        }),
        "/api/network-settings": jsonRoute(context, saveNetworkSettings),
        "/api/state/save": jsonRoute(context, async (body) => {
            const patch = parseBundleStatePatch(body.state);
            for (const [controlID, requestedPath] of Object.entries(patch.configFilePaths ?? {})) {
                configPath(findConfigEditorControl(context.manifest, controlID), requestedPath, context.bundleRoot);
            }
            return saveBundleState(patch, context.bundleRoot);
        }),
    };
    return async (request: IncomingMessage, response: ServerResponse) => {
        applySecurityHeaders(response);
        try {
            const url = new URL(request.url ?? "/", "http://localhost");
            guardRequest(request, url.pathname, { apiToken: context.apiToken, hostnames: context.hostnames ?? [] });
            const method = request.method ?? "GET";
            const headOnly = method === "HEAD";
            if (url.pathname.startsWith("/viewer-remote-data/")) {
                await requireNetworkChoice();
                await remoteViewer.serve(request, response, url.pathname);
                return;
            }
            if (url.pathname.startsWith("/viewer-data/")) {
                requireViewerLoopback(request);
                await withCurrentWorkspace(context, async () => {
                    const library = await viewerLibraries(context);
                    await viewerFiles.serve(request, response, url.pathname, library.roots, library.key);
                });
                return;
            }
            if (method === "GET" || method === "HEAD") {
                await handleGet(url, request, response, context, staticRoutes[url.pathname], headOnly);
                return;
            }
            const postRoute = postRoutes[url.pathname];
            if (method === "POST" && postRoute) {
                if (networkChoiceRoutes.has(url.pathname)) await requireNetworkChoice();
                if (independentlyGuardedRoutes.has(url.pathname)) {
                    await postRoute(request, response);
                } else {
                    await withCurrentWorkspace(context, () => postRoute(request, response), request);
                }
                return;
            }
            throw new HttpError(url.pathname in postRoutes ? 405 : 404, "Not found");
        }
        catch (error) {
            if (response.destroyed) {
                return;
            }
            if (response.headersSent) {
                if (!response.writableEnded) {
                    response.end();
                }
                return;
            }
            json(response, { error: errorMessage(error) }, error instanceof HttpError ? error.status : 500);
        }
    };
}

async function handleGet(url: URL, request: IncomingMessage, response: ServerResponse, context: RequestHandlerContext, staticRoute: RouteHandler | undefined, headOnly: boolean) {
    if (staticRoute) {
        await staticRoute(response, headOnly);
        return;
    }
    if (url.pathname === "/dev/reload" && context.enableDevReload) {
        context.addDevReloadClient(response);
        return;
    }
    if (url.pathname === "/bundle-file") {
        await serveBundleFile(response, url.searchParams.get("path") ?? "", context.bundleRoot, headOnly);
        return;
    }
    const assetPath = webuiVendorAssetPath(url.pathname, context.webRoot) ?? distModulePath(url.pathname, context.distRoot);
    if (assetPath) {
        await staticFile(assetPath, assetPath.endsWith(".js") ? "text/javascript; charset=utf-8" : contentType(assetPath), response, headOnly);
        return;
    }
    if (url.pathname === "/api/locales") {
        json(response, await loadLocaleOptions(context.repoRoot, context.bundleRoot));
        return;
    }
    if (url.pathname === "/api/manifest") {
        const before = await assertCurrentWorkspace(context);
        const locale = url.searchParams.has("locale") ? url.searchParams.get("locale") || undefined : context.defaultLocale;
        const loaded = await context.localizedBundleLoader.load(locale, preferredLocales(request.headers["accept-language"]));
        if (before !== await assertCurrentWorkspace(context)) throw new HttpError(409, "workspaceMoveStale");
        const body = {
            ...withAutoRunSetup(withAppVersion(loaded, context.appVersion)),
            networkSettings: await readNetworkSettings(),
            workspaceLocation: await usersWorkspaceLocationInfo(context.manifest, usersWorkspaceFor(context.bundleRoot)),
            workspaceMoveRevision: before,
        };
        json(response, context.enableDevReload ? { ...body, devReload: true } : body);
        return;
    }
    if (url.pathname.startsWith("/api/")) {
        throw new HttpError(404, "Not found");
    }
    notFound(response);
}

function indexToken(context: RequestHandlerContext) {
    return context.apiTokenInjectedByLauncher ? undefined : context.apiToken;
}

function jsonRoute(context: RequestHandlerContext, handler: JSONHandler) {
    return async (request: IncomingMessage, response: ServerResponse) => {
        json(response, await handler(await readBody(request, context)));
    };
}

async function readBody(request: IncomingMessage, context: RequestHandlerContext) {
    return requestObject(await readJSONBody(request, context.maxBodyBytes));
}

function commandContext(body: LooseRecord, context: RequestHandlerContext) {
    return normalizeContext(
        {
            ...parseCommandContext(body.context),
            multiPathControls: multiPathControlsFromManifest(context.manifest),
        },
        context.bundleRoot,
    );
}

function rememberDynamicActions(context: RequestHandlerContext, location: ReturnType<typeof parseManifestLocation>, payload: unknown) {
    if (!location.controlID || location.settingID) {
        return;
    }
    const record = payload != null && typeof payload === "object" ? payload as LooseRecord : {};
    const actions = record.rowActions ?? record.actions;
    const key = locationKey(location);
    if (Array.isArray(actions)) {
        context.dynamicActions.set(key, actions);
    }
    else {
        context.dynamicActions.delete(key);
    }
}

async function handleStepStream(request: IncomingMessage, response: ServerResponse, context: RequestHandlerContext, runner: StepRunner) {
    const body = await withCurrentWorkspace(context, () => readBody(request, context), request);
    if (runner === runSetup) await requireNetworkChoice(body.automatic === true ? "startup-setup" : "explicit");
    const bundle = await context.localizedBundleLoader.load(requestedLocale(body, context.defaultLocale), preferredLocales(request.headers["accept-language"]));
    await withCurrentWorkspace(context, () => streamNDJSON(
        response,
        (emit, signal) => runner(bundle.manifest, context.bundleRoot, context.runProcess, wrapEmitWithEventLog(emit), bundle.labels ?? {}, signal),
        (error) => ({ type: "complete", result: { status: "failed", results: [], error } }),
    ), request);
}

function withAppVersion(bundle: ManifestResponse, appVersion: unknown): ManifestResponse {
    const version = String(appVersion ?? "").trim();
    return version ? { ...bundle, appVersion: version } : bundle;
}

function withAutoRunSetup(bundle: ManifestResponse): ManifestResponse {
    const raw = String(process.env.WGSEXTRACT_GUI_AUTO_RUN_SETUP ?? "").trim().toLowerCase();
    return ["1", "true", "yes", "on"].includes(raw) ? { ...bundle, autoRunSetup: true } : bundle;
}

function preferredLocales(header: string | string[] | undefined) {
    return String(Array.isArray(header) ? header.join(",") : header ?? "")
        .split(",")
        .map((entry, index) => {
            const [tag = "", ...params] = entry.split(";").map((part) => part.trim());
            const qParam = params.find((part) => part.toLowerCase().startsWith("q="));
            const q = qParam ? Number(qParam.slice(2)) : 1;
            return { tag, q: Number.isFinite(q) ? q : 0, index };
        })
        .filter(({ tag, q }) => Boolean(tag) && tag !== "*" && q > 0)
        .sort((left, right) => right.q - left.q || left.index - right.index)
        .map(({ tag }) => tag);
}

function requestedLocale(body: LooseRecord, defaultLocale: string | undefined) {
    return Object.hasOwn(body, "locale") ? String(body.locale || "") || undefined : defaultLocale;
}

function webuiVendorAssetPath(pathname: string, webRoot: string) {
    const prefix = pathname.startsWith("/vendor/igv/") ? "/vendor/igv/" : "/vendor/bootstrap-icons/";
    if (!pathname.startsWith(prefix)) {
        return undefined;
    }
    const vendorRoot = path.resolve(webRoot, prefix.slice(1));
    const filePath = path.resolve(vendorRoot, pathname.slice(prefix.length));
    return filePath.startsWith(`${vendorRoot}${path.sep}`) ? filePath : undefined;
}

async function openPath(filePath: string, runProcess: RunProcess) {
    await runProcess(systemOpener(), [filePath], { cwd: filePath, env: process.env });
}

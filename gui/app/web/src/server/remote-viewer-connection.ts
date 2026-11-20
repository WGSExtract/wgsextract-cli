import { randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { mkdir, open, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { parseConfigToml, serializeConfigToml } from "./config-toml.js";
import { errnoCode, HttpError } from "./errors.js";
import type { JobServerAddress } from "./job-server-client.js";

interface Connection extends JobServerAddress { id: string }

/** Credentials live outside served bundle files and never appear in response objects. */
export class RemoteViewerConnection {
    private connection: Connection | undefined;
    private loaded = false;
    private generation = 0;
    private queue: Promise<void> = Promise.resolve();
    private readonly imports = new Map<string, number>();

    constructor(private readonly configFile: string) {}

    async status() {
        return this.serial(async () => {
            const generation = this.generation;
            if (!this.loaded) {
                try {
                    const address = await readConnection(this.configFile);
                    if (generation === this.generation) this.connection = { ...address, id: randomUUID() };
                } catch (error) {
                    if (errnoCode(error) !== "ENOENT") throw new HttpError(400, "remoteConfigurationInvalid");
                }
                this.loaded = true;
            }
            return this.connection ? { id: this.connection.id, destination: this.connection.url } : {};
        });
    }

    beginImport(owner: string) {
        if (!/^[A-Za-z0-9_-]{1,128}$/.test(owner) || this.imports.has(owner) || this.imports.size >= 8) {
            throw new HttpError(400, "remoteConfigurationInvalid");
        }
        const generation = ++this.generation;
        this.imports.set(owner, generation);
        return generation;
    }

    finishImport(owner: string) {
        this.imports.delete(owner);
    }

    cancelImport(owner: string) {
        if (this.imports.get(owner) === this.generation) this.generation++;
        this.imports.delete(owner);
    }

    cancelImports() {
        for (const owner of this.imports.keys()) this.cancelImport(owner);
    }

    async import(file: string, generation: number) {
        return this.serial(async () => {
            if (generation !== this.generation) throw new HttpError(409, "remoteConnectionChanged");
            const address = await readConnection(file);
            if (generation !== this.generation) throw new HttpError(409, "remoteConnectionChanged");
            await mkdir(path.dirname(this.configFile), { recursive: true, mode: 0o700 });
            const temporary = `${this.configFile}.${randomUUID()}`;
            try {
                await writeFile(temporary, serializeConfigToml(address), { mode: 0o600, flag: "wx" });
                await rename(temporary, this.configFile);
                if (generation !== this.generation) {
                    await rm(this.configFile, { force: true });
                    throw new HttpError(409, "remoteConnectionChanged");
                }
            } finally {
                await rm(temporary, { force: true });
            }
            this.connection = { ...address, id: randomUUID() };
            this.loaded = true;
            return { id: this.connection.id, destination: this.connection.url };
        });
    }

    async forget() {
        this.generation++;
        this.imports.clear();
        this.connection = undefined;
        this.loaded = true;
        await this.serial(() => rm(this.configFile, { force: true }));
    }

    async require(id: string): Promise<Connection> {
        await this.status();
        if (!this.connection || this.connection.id !== id) {
            throw new HttpError(401, "remoteConnectionChanged");
        }

        return this.connection;
    }

    private serial<T>(action: () => Promise<T>): Promise<T> {
        const result = this.queue.then(action);
        this.queue = result.then(() => {}, () => {});
        return result;
    }
}

async function readConnection(file: string): Promise<JobServerAddress> {
    const handle = await open(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    try {
        const info = await handle.stat();
        if (!info.isFile() || info.size > 16 * 1024) throw new HttpError(400, "remoteConfigurationInvalid");
        const bytes = Buffer.alloc(16 * 1024 + 1);
        const { bytesRead } = await handle.read(bytes, 0, bytes.length, 0);
        if (bytesRead > 16 * 1024) throw new HttpError(400, "remoteConfigurationInvalid");
        const value = parseConfigToml(bytes.subarray(0, bytesRead).toString("utf8"));
        if (typeof value.url !== "string" || typeof value.token !== "string" || !/^[A-Za-z0-9_-]{32,4096}$/.test(value.token)) {
            throw new HttpError(400, "remoteConfigurationInvalid");
        }
        const url = new URL(value.url);
        if (!["https:", "http:"].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash
            || !["", "/", "/v1", "/v1/"].includes(url.pathname)) {
            throw new HttpError(400, "remoteConfigurationInvalid");
        }
        return { url: `${url.origin}/v1`, token: value.token };
    } catch (error) {
        if (errnoCode(error) === "ENOENT") throw error;
        throw new HttpError(400, "remoteConfigurationInvalid");
    } finally {
        await handle.close();
    }
}

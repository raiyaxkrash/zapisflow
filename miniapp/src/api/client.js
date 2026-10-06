export class ApiError extends Error {
  constructor(code, message, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}
export class Api {
  // Native WebView fetch must run with the browser global as its receiver.
  constructor(fetcher = (...args) => globalThis.fetch(...args)) {
    this.fetcher = fetcher;
    this.csrf = "";
    this.botId = "";
    this.pending = new Map();
  }
  async request(path, options = {}, format = "json") {
    let result;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      result = await this.fetcher(`/api/miniapp${path}`, {
        credentials: "same-origin",
        ...options,
        signal: controller.signal,
        headers: { "X-MiniApp-Bot": this.botId, ...options.headers },
      });
    } catch {
      throw new ApiError(
        "NETWORK",
        "Нет соединения. Проверьте интернет и повторите запрос",
      );
    } finally {
      clearTimeout(timeout);
    }
    if (result.ok && format === "image") {
      const blob = await result.blob();
      if (blob.type !== "image/jpeg" || blob.size > 8 * 1024 * 1024)
        throw new ApiError("IMAGE_INVALID", "Изображение недоступно");
      return blob;
    }
    let data;
    try {
      data = await result.json();
    } catch {
      throw new ApiError(
        "NETWORK",
        "Не удалось прочитать ответ. Повторите запрос",
      );
    }
    if (!result.ok)
      throw new ApiError(
        data.code || "UNAVAILABLE",
        data.message || "Не удалось выполнить действие",
        result.status,
      );
    return data;
  }
  async auth(botId, initData) {
    this.botId = botId;
    const data = await this.request("/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bot_public_id: botId, init_data: initData }),
    });
    this.csrf = data.csrf_token;
    return data;
  }
  image(path) {
    return this.request(path, {}, "image");
  }
  get(path) {
    return this.request(path);
  }
  async mutate(path, body, method = "POST") {
    const fingerprint = method + path + JSON.stringify(body);
    const key = this.pending.get(fingerprint) || crypto.randomUUID();
    this.pending.set(fingerprint, key);
    const result = await this.request(path, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": this.csrf,
        "Idempotency-Key": key,
      },
      body: JSON.stringify(body),
    });
    this.pending.delete(fingerprint);
    return result;
  }
  upload(path, file, progress) {
    return new Promise((resolve, reject) => {
      const fingerprint = path + file.name + file.size + file.lastModified;
      const key = this.pending.get(fingerprint) || crypto.randomUUID();
      this.pending.set(fingerprint, key);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/miniapp${path}`);
      xhr.timeout = 60000;
      xhr.setRequestHeader("X-MiniApp-Bot", this.botId);
      xhr.setRequestHeader("X-CSRF-Token", this.csrf);
      xhr.setRequestHeader("Idempotency-Key", key);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable)
          progress(Math.round((e.loaded / e.total) * 100));
      };
      xhr.onerror = () =>
        reject(
          new ApiError(
            "NETWORK",
            "Не удалось загрузить файл. Повторите попытку",
          ),
        );
      xhr.ontimeout = () =>
        reject(
          new ApiError(
            "NETWORK",
            "Загрузка заняла слишком много времени. Повторите попытку",
          ),
        );
      xhr.onload = () => {
        let data;
        try {
          data = JSON.parse(xhr.responseText);
        } catch {
          data = {};
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          this.pending.delete(fingerprint);
          resolve(data);
        } else
          reject(
            new ApiError(
              data.code || "UPLOAD_FAILED",
              data.message || "Не удалось загрузить файл",
              xhr.status,
            ),
          );
      };
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    });
  }
}

// Обмен с сервером.

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.status = status;
  }
}

const listeners = {};
export const on = (name, fn) => { (listeners[name] ||= []).push(fn); };
export const emit = (name, data) => (listeners[name] || []).forEach((fn) => fn(data));

function explain(data, status) {
  if (data && typeof data.detail === 'string') return data.detail;
  if (data && Array.isArray(data.detail)) {
    const type = data.detail[0]?.type || '';
    if (type.includes('too_short') || type === 'missing') return 'Поле пустое или значение слишком короткое. Скопируйте его целиком';
    if (type.includes('too_long')) return 'Значение слишком длинное';
    return 'Проверьте, что всё введено верно';
  }
  if (status === 429) return 'Слишком много попыток, подождите немного';
  if (status >= 500) return 'Сервер сейчас не справился. Попробуйте ещё раз';
  return 'Не получилось. Попробуйте ещё раз';
}

export async function api(path, { method = 'GET', body } = {}) {
  let res;
  try {
    res = await fetch('/api' + path, {
      method,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'leadscout' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError('Нет связи с сервером. Проверьте интернет', 0);
  }
  let data = null;
  try { data = await res.json(); } catch { /* пустой ответ */ }
  if (res.status === 401 && path !== '/login') {
    emit('unauth');
    throw new ApiError('Нужно войти', 401);
  }
  if (!res.ok) throw new ApiError(explain(data, res.status), res.status);
  return data;
}

export const get = (p) => api(p);
export const post = (p, body = {}) => api(p, { method: 'POST', body });
export const put = (p, body = {}) => api(p, { method: 'PUT', body });
export const patch = (p, body = {}) => api(p, { method: 'PATCH', body });
export const del = (p) => api(p, { method: 'DELETE' });

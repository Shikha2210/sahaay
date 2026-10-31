// Builds an UNSIGNED JWT-shaped string. The frontend only reads `exp` for UX; the server is the authority.
const b64 = o => btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
export const makeToken = (secondsFromNow, extra = {}) =>
  `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64({ sub: 'x', exp: Math.floor(Date.now() / 1000) + secondsFromNow, ...extra })}.sig`;

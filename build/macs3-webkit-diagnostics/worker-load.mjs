import { executeMACS3 } from './execute-load.mjs';

const isNode = typeof process !== 'undefined' && !!process.versions?.node;
if (!isNode) {
  self.addEventListener('unhandledrejection', event => console.log('[MACS3 load] unhandled rejection ' + (event.reason?.stack || event.reason)));
  self.addEventListener('error', event => console.log('[MACS3 load] worker error ' + event.message));
}
let post, receive;
if (isNode) {
  const { parentPort } = await import('node:worker_threads');
  post = (message, transfer) => parentPort.postMessage(message, transfer);
  receive = callback => parentPort.once('message', callback);
} else {
  post = (message, transfer) => self.postMessage(message, transfer);
  receive = callback => self.addEventListener('message', event => callback(event.data), { once: true });
}

receive(async request => {
  try {
    const result = await executeMACS3(request);
    const transfer = [...new Set([result.stdout.buffer, result.stderr.buffer,
      ...result.files.map(file => file.data.buffer)])];
    console.log('[MACS3 diagnostic] posting result ' + JSON.stringify(transfer.map(buffer=>buffer.byteLength)));
    post({ type: 'result', result }, transfer);
    console.log('[MACS3 diagnostic] result posted');
  } catch (error) {
    post({ type: 'error', error: { name: error?.name || 'Error',
      message: error?.message || String(error), stack: error?.stack } });
  }
});

import { executeCutadapt } from './cutadapt-execute.mjs';

const isNode = typeof process !== 'undefined' && !!process.versions?.node;
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
    const result = await executeCutadapt(request);
    const transfer = [...new Set([result.stdout.buffer, result.stderr.buffer,
      ...result.files.map(file => file.data.buffer)])];
    post({ type: 'result', result }, transfer);
  } catch (error) {
    post({ type: 'error', error: { name: error?.name || 'Error',
      message: error?.message || String(error), stack: error?.stack } });
  }
});

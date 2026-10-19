import { fileURLToPath } from 'node:url';
import { resolve, join } from 'node:path';
import { ApplicationError, commandMain, invokeCLI, privateDirectory } from './adapter-runtime.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const cases = new Set(['starter', 'missing-approval', 'already-completed']);
const id = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value);

export async function runApplication(input, parameters, signal, invocation) {
  if (Object.keys(input).some(key => !['caseId', 'ticketId', 'approvalId'].includes(key)) || !cases.has(input.caseId) ||
      !id(input.ticketId) || !(input.approvalId === null || id(input.approvalId))) {
    throw new Error('Supply a reviewed case, exact ticket ID and explicit caller approval ID or null.');
  }
  const directory = await privateDirectory(root, invocation);
  const state = join(directory, 'workspace.json');
  const cli = ['uv', 'run', '--locked', '--no-sync', 'resolve-refund'];
  try {
    await invokeCLI([...cli, '--init-case', input.caseId, '--workspace', state], { cwd: root, signal });
    const argv = [...cli, input.ticketId, '--workspace', state, '--output-root', join(directory, 'evidence')];
    if (input.approvalId !== null) argv.push('--approval-id', input.approvalId);
    if (parameters.model) argv.push('--model', parameters.model);
    const output = await invokeCLI(argv, { cwd: root, signal });
    return { application: output, localState: { directory, workspaceFile: state } };
  } catch (error) {
    if (error instanceof ApplicationError) error.evidence.runDirectory = directory;
    throw error;
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await commandMain(runApplication);
}

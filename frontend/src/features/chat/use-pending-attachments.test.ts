import { act, renderHook } from '@testing-library/react';

import { MAX_ATTACHMENT_BYTES, usePendingAttachments } from './use-pending-attachments';

// jsdom doesn't implement URL.createObjectURL/revokeObjectURL at all, so
// there's nothing for vi.spyOn to wrap -- assign plain mocks once. Left in
// place (not restored) across the file: React's unmount cleanup for a given
// test's hook can run asynchronously, after that test's own afterEach would
// otherwise have torn the mocks back down, so per-test restore races with it.
URL.createObjectURL = vi.fn();
URL.revokeObjectURL = vi.fn();

beforeEach(() => {
  let n = 0;
  vi.mocked(URL.createObjectURL).mockImplementation(() => `blob:mock-${n++}`);
  vi.mocked(URL.revokeObjectURL).mockClear();
});

function imageFile(name = 'photo.png'): File {
  return new File(['x'], name, { type: 'image/png' });
}

function docFile(name = 'notes.txt'): File {
  return new File(['x'], name, { type: 'text/plain' });
}

function oversizeFile(name = 'huge.bin'): File {
  return new File([new Uint8Array(MAX_ATTACHMENT_BYTES + 1)], name, { type: 'application/octet-stream' });
}

test('picking an image file adds it with a preview URL', () => {
  const { result } = renderHook(() => usePendingAttachments());
  act(() => result.current.addFiles([imageFile()]));
  expect(result.current.files).toHaveLength(1);
  expect(result.current.files[0]!.file.name).toBe('photo.png');
  expect(result.current.files[0]!.previewUrl).toBe('blob:mock-0');
  expect(result.current.error).toBeNull();
});

test('picking a non-image file adds it with no preview URL', () => {
  const { result } = renderHook(() => usePendingAttachments());
  act(() => result.current.addFiles([docFile()]));
  expect(result.current.files).toHaveLength(1);
  expect(result.current.files[0]!.previewUrl).toBeNull();
});

test('an oversize file is rejected with an inline error and not added', () => {
  const { result } = renderHook(() => usePendingAttachments());
  act(() => result.current.addFiles([oversizeFile()]));
  expect(result.current.files).toHaveLength(0);
  expect(result.current.error).toContain('huge.bin');
  expect(result.current.error).toContain('50 MB');
});

test('removing a file revokes its preview URL and drops it from the list', () => {
  const { result } = renderHook(() => usePendingAttachments());
  act(() => result.current.addFiles([imageFile()]));
  const id = result.current.files[0]!.id;
  act(() => result.current.remove(id));
  expect(result.current.files).toHaveLength(0);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock-0');
});

test('clear revokes every outstanding preview URL and empties the list', () => {
  const { result } = renderHook(() => usePendingAttachments());
  act(() => result.current.addFiles([imageFile('a.png'), imageFile('b.png')]));
  act(() => result.current.clear());
  expect(result.current.files).toHaveLength(0);
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
});

test('a valid file added alongside a rejected one is still kept', () => {
  const { result } = renderHook(() => usePendingAttachments());
  act(() => result.current.addFiles([docFile('ok.txt'), oversizeFile()]));
  expect(result.current.files.map((f) => f.file.name)).toEqual(['ok.txt']);
  expect(result.current.error).toContain('huge.bin');
});

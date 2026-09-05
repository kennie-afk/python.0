export interface FormState {
  error: string | null;
  message: string | null;
}

export const idleForm: FormState = { error: null, message: null };

export function failed(error: string): FormState {
  return { error, message: null };
}

export function succeeded(message: string): FormState {
  return { error: null, message };
}

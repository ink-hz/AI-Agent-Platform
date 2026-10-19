/** Keep one write identity while the same form is retried. */
export class RequestIdentity {
  private fingerprint = "";
  private id = "";

  constructor(private readonly create: () => string = () => crypto.randomUUID()) {}

  forPayload(payload: object): string {
    const fingerprint = JSON.stringify(payload);
    if (!this.id || this.fingerprint !== fingerprint) {
      this.id = this.create();
      this.fingerprint = fingerprint;
    }
    return this.id;
  }

  clear(): void {
    this.id = "";
    this.fingerprint = "";
  }
}

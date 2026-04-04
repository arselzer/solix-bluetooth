export interface SolixPacket {
  header: number;
  length: number;
  pattern: Uint8Array;
  command: Uint8Array;
  payload: Uint8Array;
  checksum: number;
  raw: Uint8Array;
}

export interface TelemetryData {
  [key: string]: string | number;
}

export type ConnectionState = 'disconnected' | 'connecting' | 'negotiating' | 'connected';

export interface LogEntry {
  timestamp: number;
  direction: 'tx' | 'rx' | 'info' | 'error';
  message: string;
  data?: string;
}

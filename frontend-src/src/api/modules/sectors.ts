/** 板块域：GET /api/sectors · GET /api/sectors/{id}/iv-ranking */
import { get, mockOr } from '../client';
import { unwrap } from '../live';
import * as fx2 from '@/mocks/fixtures2';
import type { IvRankRow, Sector } from '../types';

export const sectorsApi = {
  // 契约 {sectors:[{id,name,tickers[]}]}：解包信封；行内 snake_case 由 sectors/model normalizeSector 兼容
  list: (): Promise<Sector[]> => mockOr(() => fx2.getSectors(), () => get('/sectors').then((d) => unwrap(d, 'sectors') as unknown as Sector[])),
  // 契约 {sector_id, sector_name, rankings:[...]} → rankings；行字段由 normalizeIvRow 兼容
  ivRanking: (sectorId: string): Promise<IvRankRow[]> =>
    mockOr(
      () => fx2.getSectorIvRanking(sectorId),
      () => get(`/sectors/${encodeURIComponent(sectorId)}/iv-ranking`).then((d) => unwrap(d, 'rankings') as unknown as IvRankRow[]),
    ),
};

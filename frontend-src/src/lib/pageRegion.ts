/** 页面主业务区状态。由真实渲染分支写入，测速分类器只读这些标记。 */
export type PageRegionState = 'loading' | 'idle' | 'empty' | 'error' | 'content';

export function pageRegionProps(region: string, state: PageRegionState): {
  'data-optix-region': string;
  'data-optix-state': PageRegionState;
} {
  return {
    'data-optix-region': region,
    'data-optix-state': state,
  };
}

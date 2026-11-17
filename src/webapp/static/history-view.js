// Presentation only: historical measurement targets never share a plotted series.
export function historyView(rows,requested='visible_leaf_lamina'){
 const targetOf=row=>row.measurement_target||'visible_plant_foreground';
 const hasLamina=rows.some(row=>targetOf(row)==='visible_leaf_lamina');
 const hasLegacy=rows.some(row=>targetOf(row)==='visible_plant_foreground');
 const target=requested==='visible_plant_foreground'||!hasLamina&&hasLegacy?'visible_plant_foreground':'visible_leaf_lamina';
 return {target,hasLegacy,rows:rows.filter(row=>targetOf(row)===target)};
}

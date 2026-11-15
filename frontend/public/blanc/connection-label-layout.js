/* Presentation only. Original entity labels and evidence references are retained. */
globalThis.BlancGraphLabels = (() => {
  function describe(node) {
    const original = node.name;
    let title = original;
    let kind = node.type.charAt(0).toUpperCase() + node.type.slice(1);
    let identifier = '';
    let source = '';
    const account = original.match(/^(.*?)\s*\(ID\s+(\d+)\)$/i);
    const channel = original.match(/^Discord Channel\s+(\d+)$/i);
    if (account) { title = account[1]; identifier = account[2]; }
    else if (channel) { title = 'Discord channel'; kind = 'Channel'; identifier = channel[1]; }
    else if (node.type === 'document' || /^(?:Attachment|Local File)\s+/i.test(original)) {
      title = original.replace(/^(?:Attachment|Local File)\s+/i, '').split(/[\\/]+/).pop() || original;
      source = title;
      const stem = title.replace(/\.[a-z0-9]{1,8}$/i, '');
      title = /^flight[ _-]*bookings?$/i.test(stem) ? 'Flight booking' : stem.replace(/([a-z])([A-Z])/g,'$1 $2').replace(/[_-]+/g,' ').replace(/\s+/g,' ').trim();
      title = title.charAt(0).toUpperCase() + title.slice(1);
      kind = /^Attachment\s/i.test(original) ? 'Attachment' : 'File';
    } else if (node.type === 'device') {
      title = original.replace(/\s*\((?:Users[\\/]|[A-Z]:[\\/]).*\)$/i, '');
    }
    // Preserve the extension if a filename needs abbreviation.
    if (title.length > 32 && !title.includes(' ')) title = title.slice(0, 17) + '…' + title.slice(-11);
    return { title, kind, identifier, original, source: source || (identifier ? 'ID '+identifier : '') };
  }
  function lines(text, measure, maxWidth = 180) {
    if (measure(text) <= maxWidth) return [text];
    const words = text.split(/\s+/), out = []; let line = '';
    for (const word of words) {
      if (line && measure(line + ' ' + word) > maxWidth) { out.push(line); line = word; }
      else line = line ? line + ' ' + word : word;
    }
    if (line) out.push(line);
    return out;
  }
  function overlap(a, b) { return Math.max(0, Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x))*Math.max(0, Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y)); }
  function place(anchor, size, occupied, bounds) {
    const { w, h } = size, gap = 25;
    const candidates = [
      {x:anchor.x-w/2,y:anchor.y+gap},
      {x:anchor.x-w/2,y:anchor.y-gap-h},
      {x:anchor.x+gap,y:anchor.y-h/2},
      {x:anchor.x-gap-w,y:anchor.y-h/2},
      {x:anchor.x+gap,y:anchor.y+gap},
      {x:anchor.x-gap-w,y:anchor.y+gap},
      {x:anchor.x+gap,y:anchor.y-gap-h},
      {x:anchor.x-gap-w,y:anchor.y-gap-h},
    ];
    return candidates.map((p,i)=>{
      const box={x:Math.max(8,Math.min(bounds.w-w-8,p.x)),y:Math.max(50,Math.min(bounds.h-h-38,p.y)),w,h};
      return { ...box, score:occupied.reduce((sum,o)=>sum+overlap(box,o),0)*20+i*4+Math.hypot(box.x-p.x,box.y-p.y)*2 };
    }).sort((a,b)=>a.score-b.score)[0];
  }
  return {describe,lines,place};
})();

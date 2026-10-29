/**
 * VirtualList — lightweight windowed list renderer (no external deps).
 *
 * Renders only the items currently visible inside a fixed-height scroll
 * container, plus an `overscan` buffer above and below. All items must have
 * the same height (`itemHeight` in pixels).
 *
 * Usage:
 *   <VirtualList
 *     items={myArray}
 *     itemHeight={56}
 *     containerHeight={400}
 *     renderItem={(item, index) => <MyCard key={item.id} item={item} />}
 *   />
 */

import React, { useRef, useState, useCallback, useLayoutEffect } from 'react';

interface VirtualListProps<T> {
  items: T[];
  /** Fixed height of each row in pixels (includes gap below). */
  itemHeight: number;
  /** Gap between items in pixels. Applied as padding-bottom on the slot. Default: 8. */
  gap?: number;
  /** Visible window height in pixels. Leave undefined to fill parent (uses ResizeObserver). */
  containerHeight?: number;
  /** Extra rows rendered above and below the visible window. Default: 3. */
  overscan?: number;
  /** Render function for each item. */
  renderItem: (item: T, index: number) => React.ReactNode;
  className?: string;
}

export function VirtualList<T>({
  items,
  itemHeight,
  gap = 8,
  containerHeight: fixedHeight,
  overscan = 3,
  renderItem,
  className,
}: VirtualListProps<T>) {
  const outerRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [measuredHeight, setMeasuredHeight] = useState(fixedHeight ?? 400);

  // Measure container height when not fixed
  useLayoutEffect(() => {
    if (fixedHeight !== undefined) return;
    const el = outerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      setMeasuredHeight(entry.contentRect.height);
    });
    ro.observe(el);
    setMeasuredHeight(el.clientHeight);
    return () => ro.disconnect();
  }, [fixedHeight]);

  const height = fixedHeight ?? measuredHeight;
  const totalHeight = items.length * itemHeight;

  const handleScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    setScrollTop(e.currentTarget.scrollTop);
  }, []);

  const startIndex = Math.max(0, Math.floor(scrollTop / itemHeight) - overscan);
  const endIndex = Math.min(
    items.length - 1,
    Math.ceil((scrollTop + height) / itemHeight) + overscan
  );

  const visibleItems = [];
  for (let i = startIndex; i <= endIndex; i++) {
    visibleItems.push(
      <div
        key={i}
        style={{
          position: 'absolute',
          top: i * itemHeight,
          left: 0,
          right: 0,
          height: itemHeight,
          paddingBottom: gap,
        }}
      >
        {renderItem(items[i], i)}
      </div>
    );
  }

  return (
    <div
      ref={outerRef}
      onScroll={handleScroll}
      className={className}
      style={{ overflowY: 'auto', height: fixedHeight !== undefined ? height : '100%', position: 'relative' }}
    >
      {/* Total height spacer */}
      <div style={{ height: totalHeight, position: 'relative' }}>
        {visibleItems}
      </div>
    </div>
  );
}

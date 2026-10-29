import React, { useState, useMemo, useEffect, useRef } from 'react';
import { ArrowDownAZ, ArrowUpAZ, Search, Filter } from 'lucide-react';
import clsx from 'clsx';

interface Column<T> {
  key: keyof T;
  label: string;
  render?: (value: any, item: T) => React.ReactNode;
  sortable?: boolean;
  filterable?: boolean; // Enables simple unique value dropdown
}

interface DataTableProps<T> {
  data: T[];
  columns: Column<T>[];
  className?: string;
  maxHeightClass?: string;
  onRowClick?: (item: T, event: React.MouseEvent<HTMLTableRowElement>) => void;
  getRowClassName?: (item: T) => string;
  rowKey?: (item: T) => string | number;
  scrollToKey?: string | number | null;
  onFiltersChange?: (filters: Record<string, string>, searchTerm: string) => void;
}

export function DataTable<T extends Record<string, any>>({
  data,
  columns,
  className,
  maxHeightClass,
  onRowClick,
  getRowClassName,
  rowKey,
  scrollToKey,
  onFiltersChange,
}: DataTableProps<T>) {
  const [searchTerm, setSearchTerm] = useState('');
  const [sortConfig, setSortConfig] = useState<{ key: keyof T; direction: 'asc' | 'desc' } | null>(null);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const tableContainerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    onFiltersChange?.(filters, searchTerm);
  }, [filters, searchTerm, onFiltersChange]);

  // 1. Filter Logic
  const filteredData = useMemo(() => {
    return data.filter(item => {
      // Global Search
      const matchesSearch = Object.values(item).some(val => 
        String(val).toLowerCase().includes(searchTerm.toLowerCase())
      );
      if (!matchesSearch) return false;

      // Column Filters
      for (const key in filters) {
        if (filters[key] && String(item[key]) !== filters[key]) {
          return false;
        }
      }
      return true;
    });
  }, [data, searchTerm, filters]);

  // 2. Sort Logic
  const sortedData = useMemo(() => {
    if (!sortConfig) return filteredData;
    return [...filteredData].sort((a, b) => {
      if (a[sortConfig.key] < b[sortConfig.key]) return sortConfig.direction === 'asc' ? -1 : 1;
      if (a[sortConfig.key] > b[sortConfig.key]) return sortConfig.direction === 'asc' ? 1 : -1;
      return 0;
    });
  }, [filteredData, sortConfig]);

  const handleSort = (key: keyof T) => {
    setSortConfig(current => {
      if (current?.key === key) {
        return current.direction === 'asc' ? { key, direction: 'desc' } : null;
      }
      return { key, direction: 'asc' };
    });
  };

  // Get unique values for filters
  const getUniqueValues = (key: keyof T) => {
    const values = new Set(data.map(item => String(item[key])));
    return Array.from(values);
  };

  useEffect(() => {
    if (scrollToKey == null || !rowKey || !tableContainerRef.current) return;
    const keyString = String(scrollToKey);
    const safeKey = keyString.replace(/"/g, '\\"');
    const row = tableContainerRef.current.querySelector<HTMLTableRowElement>(
      `tr[data-row-key="${safeKey}"]`
    );
    if (row) {
      row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, [scrollToKey, rowKey, sortedData]);

  return (
    <div className={clsx("w-full", className)}>
      {/* Controls */}
      <div className="flex flex-col sm:flex-row gap-4 mb-4 justify-between">
        <div className="relative w-full sm:w-64">
           <Search size={16} className="absolute left-3 top-2.5 text-[#525F6B]" />
           <input 
             type="text" 
             placeholder="Search..." 
             value={searchTerm}
             onChange={e => setSearchTerm(e.target.value)}
             className="w-full pl-9 pr-4 py-2 border border-[#BFC0C2] rounded-sm focus:border-[#007BC0] focus:outline-none text-sm"
           />
        </div>
        
        {/* Active Filters Display */}
        {Object.keys(filters).length > 0 && (
           <div className="flex gap-2 items-center">
             <span className="text-xs text-[#525F6B]">Filters:</span>
             {Object.entries(filters).map(([key, val]) => (
                <button 
                  key={key} 
                  onClick={() => {
                    const newFilters = {...filters};
                    delete newFilters[key];
                    setFilters(newFilters);
                  }}
                  className="bg-[#EFF1F2] text-xs px-2 py-1 rounded flex items-center gap-1 hover:bg-[#BFC0C2]"
                >
                  {String(key)}: <strong>{val}</strong> <span className="text-[#000000]">×</span>
                </button>
             ))}
           </div>
        )}
      </div>

      {/* Table */}
      <div
        ref={tableContainerRef}
        className={clsx(
          "overflow-x-auto border border-[#BFC0C2] rounded-sm",
          maxHeightClass ? `overflow-y-auto ${maxHeightClass}` : "overflow-y-auto max-h-[360px]"
        )}
      >
        <table className="w-full text-sm text-left">
          <thead className="bg-[#EFF1F2] text-[#000000] uppercase text-xs sticky top-0 z-10">
            <tr>
              {columns.map(col => (
                <th key={String(col.key)} className="px-4 py-2 font-semibold">
                  <div className="flex items-center gap-2">
                    <span 
                      className={clsx("cursor-pointer select-none flex items-center gap-1", col.sortable && "hover:text-[#007BC0]")}
                      onClick={() => col.sortable && handleSort(col.key)}
                    >
                      {col.label}
                      {sortConfig?.key === col.key && (
                        sortConfig.direction === 'asc' ? <ArrowUpAZ size={14} /> : <ArrowDownAZ size={14} />
                      )}
                    </span>
                    
                    {col.filterable && (
                      <div className="relative group ml-auto">
                        <Filter size={12} className="text-[#525F6B] cursor-pointer hover:text-[#000000]" />
                        <select 
                           className="absolute right-0 top-0 opacity-0 w-6 h-6 cursor-pointer"
                           onChange={(e) => {
                             if(e.target.value) setFilters({...filters, [String(col.key)]: e.target.value});
                           }}
                           value={filters[String(col.key)] || ''}
                        >
                           <option value="">All</option>
                           {getUniqueValues(col.key).map(v => <option key={v} value={v}>{v}</option>)}
                        </select>
                      </div>
                    )}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
           <tbody className="divide-y divide-[#EFF1F2]">
              {sortedData.length > 0 ? (
               sortedData.map((item, idx) => {
                 const resolvedKey = rowKey ? rowKey(item) : idx;
                 return (
                  <tr
                    key={resolvedKey}
                    data-row-key={String(resolvedKey)}
                    className={clsx(
                      "hover:bg-[#F5F7F7]",
                      getRowClassName ? getRowClassName(item) : "",
                      onRowClick ? "cursor-pointer" : ""
                    )}
                    onClick={(event) => onRowClick?.(item, event)}
                  >
                    {columns.map(col => (
                      <td key={String(col.key)} className="px-4 py-3">
                        {col.render ? col.render(item[col.key], item) : String(item[col.key])}
                      </td>
                    ))}
                  </tr>
                 );
               })
              ) : (
               <tr>
                 <td colSpan={columns.length} className="px-4 py-8 text-center text-[#525F6B]">
                   No results found
                 </td>
               </tr>
             )}
          </tbody>
          <tfoot className="bg-[#EFF1F2] text-[#525F6B] text-xs sticky bottom-0 z-10">
            <tr>
              <td colSpan={columns.length} className="px-4 py-2 text-right">
                Showing {sortedData.length} of {data.length} entries
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

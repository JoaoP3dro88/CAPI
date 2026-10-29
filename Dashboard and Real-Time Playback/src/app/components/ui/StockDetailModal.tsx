import React from 'react';
import { createPortal } from 'react-dom';
import { motion } from 'motion/react';
import { X, Layers } from 'lucide-react';
import { StockInfo, BOSCH_COLORS } from '../../data';
import { DataTable } from './DataTable';

interface StockDetailModalProps {
  stock: StockInfo;
  onClose: () => void;
}

export const StockDetailModal: React.FC<StockDetailModalProps> = ({
  stock,
  onClose,
}) => {
  const displayLineId = (lineId: string) => {
    if (lineId.startsWith('Agie')) {
      const number = lineId.replace('Agie', '').trim();
      return `Corredor ${number}`;
    }
    return lineId;
  };
  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm p-8">
      <motion.div 
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        className="bg-white w-full max-w-4xl max-h-[80vh] rounded-lg shadow-2xl flex flex-col overflow-hidden"
      >
        {/* Header */}
        <div className="bg-[#EFF1F2] px-6 py-4 border-b border-[#BFC0C2] flex justify-between items-center">
           <div className="flex items-center gap-4">
              <h2 className="text-xl font-bold text-[#000000] flex items-center gap-2">
                 <Layers className="text-[#007BC0]" /> 
                 Stock Details: <span className="text-[#007BC0]">{displayLineId(stock.line_id)}</span>
              </h2>
              <div className="bg-white px-3 py-1 rounded text-sm border border-[#BFC0C2]">
                  <strong>{stock.trays}</strong> Trays Total
                     {typeof stock.parts === 'number' && (
                       <span className="ml-2">· <strong>{stock.parts}</strong> Parts</span>
                     )}
              </div>
           </div>
           <button 
             onClick={onClose}
             className="p-2 hover:bg-[#BFC0C2] rounded-full transition-colors"
           >
             <X size={24} className="text-[#525F6B]" />
           </button>
        </div>

        {/* Body */}
        <div className="flex-grow p-6 overflow-hidden">
           {stock.tray_details.length > 0 ? (
             <DataTable 
               data={stock.tray_details}
               maxHeightClass="max-h-[50vh]"
               columns={[
                 { key: 'tray_id', label: 'Tray ID', sortable: true },
                 { key: 'part_number_id', label: 'Part Number', sortable: true, filterable: true },
                 { key: 'pieces', label: 'Quantity (pcs)', sortable: true, render: (val) => (
                   <span className="font-mono font-bold text-[#007BC0]">{val ?? '—'}</span>
                 )},
               ]}
             />
           ) : (
             <div className="h-64 flex flex-col items-center justify-center text-[#BFC0C2]">
               <Layers size={48} className="mb-4 opacity-50" />
               <p>No trays currently in stock for this line.</p>
             </div>
           )}
        </div>
      </motion.div>
    </div>,
    document.body
  );
};

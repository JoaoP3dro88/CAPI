import React from 'react';
import clsx from 'clsx';

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  title?: string;
  subtitle?: string;
  action?: React.ReactNode;
}

export const Card: React.FC<CardProps> = ({ title, subtitle, action, className, children, ...props }) => {
  return (
    <div 
      className={clsx(
        "bg-white border-l-4 border-l-[#BFC0C2] shadow-sm p-4 relative",
        className
      )}
      {...props}
    >
      {(title || action) && (
        <div className="flex items-start justify-between mb-4">
          <div>
            {title && <h3 className="text-sm font-bold uppercase tracking-wide text-[#000000]">{title}</h3>}
            {subtitle && <p className="text-xs text-[#525F6B] mt-1">{subtitle}</p>}
          </div>
          {action && <div>{action}</div>}
        </div>
      )}
      <div className="text-sm">
        {children}
      </div>
    </div>
  );
};

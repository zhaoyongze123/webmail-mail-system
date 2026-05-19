import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table';
import type { ReactNode } from 'react';
import type { PaginationMeta } from '../types';

export function AdminListTable<TData extends object>({
  data,
  columns,
  emptyMessage,
  toolbar,
  pagination,
}: {
  data: TData[];
  columns: ColumnDef<TData>[];
  emptyMessage: string;
  toolbar?: ReactNode;
  pagination?: PaginationMeta & {
    onPageChange: (page: number) => void;
  };
}) {
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="admin-table-panel">
      {toolbar ? <div className="admin-table-toolbar">{toolbar}</div> : null}
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th key={header.id}>
                    {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length}>{emptyMessage}</td>
              </tr>
            ) : (
              table.getRowModel().rows.map((row) => (
                <tr key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {pagination ? (
        <div className="admin-pagination">
          <span>
            第 {pagination.page} / {Math.max(pagination.total_pages, 1)} 页，共 {pagination.total} 条
          </span>
          <div className="admin-inline-actions">
            <button
              type="button"
              className="admin-button admin-button-secondary"
              disabled={pagination.page <= 1}
              onClick={() => pagination.onPageChange(pagination.page - 1)}
            >
              上一页
            </button>
            <button
              type="button"
              className="admin-button admin-button-secondary"
              disabled={pagination.page >= pagination.total_pages}
              onClick={() => pagination.onPageChange(pagination.page + 1)}
            >
              下一页
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

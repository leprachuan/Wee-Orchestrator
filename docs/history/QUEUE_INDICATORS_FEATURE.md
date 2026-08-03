# Queue Status Indicators Feature

## Overview

The queue status indicator system provides visual feedback for each queued request, showing its processing state with color-coded dots and animations, similar to Copilot and Claude interfaces.

## Status Indicator States

| Status | Symbol | Color | Meaning |
|--------|--------|-------|---------|
| **Pending** | ◯ (empty) | Gray (#999) | Waiting in queue to be processed |
| **Processing** | ◐ (half) | Amber (#ffc107) | Currently being processed by AI (animated pulse) |
| **Completed** | ● (filled) | Green (#00c853) | Successfully processed |
| **Failed** | ● (filled) | Red (#f5576c) | Processing encountered an error |

## Implementation Details

### State Management

**New STATE field:**
```javascript
STATE.currentProcessingQueueId  // Tracks which queue item is currently being processed
```

**Queue item structure:**
```javascript
{
  id: "queue-abc123",
  text: "user message",
  files: [],
  timestamp: 1708642065000,
  status: "pending" | "processing" | "completed" | "failed"
}
```

### Core Functions

#### `setQueueItemStatus(queueId, status)`
Updates the status of a specific queue item and re-renders the queue panel.

```javascript
setQueueItemStatus('queue-abc123', 'processing');
```

#### `markCurrentQueueAsProcessing(queueId)`
Marks an item as currently processing and sets it as the active item.

```javascript
markCurrentQueueAsProcessing(item.id);
```

#### `markCurrentQueueAsCompleted()`
Marks the current processing item as completed and clears the processing state.

```javascript
markCurrentQueueAsCompleted();
```

#### `markCurrentQueueAsFailed()`
Marks the current processing item as failed and clears the processing state.

```javascript
markCurrentQueueAsFailed();
```

### Flow Integration

1. **User sends first message** → Normal processing
2. **User sends second message while first is processing** → Message queued with `status: 'pending'`
3. **Multiple messages queued** → Each shows gray ◯ dot
4. **First message completes** → Auto-submit next item
5. **Item is pulled from queue** → Status changed to `'processing'`, dot becomes amber ◐ (pulsing)
6. **Processing completes** → Status changed to `'completed'`, dot becomes green ●
7. **Next item auto-submits** → Cycle repeats
8. **Error occurs** → Status changed to `'failed'`, dot becomes red ●

## CSS Styling

### Status Indicator Classes

```css
.queue-status-dot {
  /* 14px circle, transitions smoothly */
  width: 14px;
  height: 14px;
  transition: all 0.3s;
}

.queue-status-pending .queue-status-dot {
  color: #999;  /* Gray */
}

.queue-status-processing .queue-status-dot {
  color: #ffc107;  /* Amber with pulse animation */
  animation: pulse 1.5s ease-in-out infinite;
}

.queue-status-completed .queue-status-dot {
  color: #00c853;  /* Green */
}

.queue-status-failed .queue-status-dot {
  color: #f5576c;  /* Red */
}
```

### Animation

**Pulse effect** (for processing state):
```css
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
```

Pulses every 1.5 seconds to indicate active processing.

## Usage Examples

### Queueing a Request

```javascript
const item = queueRequest("What is the weather?", []);
// item.status === 'pending' ✓
// Gray ◯ dot appears in queue panel
```

### Processing a Queued Item

```javascript
markCurrentQueueAsProcessing(item.id);
// item.status === 'processing' ✓
// Amber ◐ dot with pulse animation appears
```

### Completing Successfully

```javascript
markCurrentQueueAsCompleted();
// item.status === 'completed' ✓
// Green ● dot appears
// Auto-submits next item if not paused
```

### Handling Error

```javascript
try {
  await sendMessage();
} catch (err) {
  markCurrentQueueAsFailed();
  // item.status === 'failed' ✓
  // Red ● dot appears
}
```

## Testing Checklist

### Visual Indicators
- [ ] Pending items show gray ◯ dots
- [ ] Processing items show amber ◐ dots
- [ ] Processing dots have visible pulse animation
- [ ] Completed items show green ● dots
- [ ] Failed items show red ● dots

### State Transitions
- [ ] Item starts as pending
- [ ] Item transitions to processing when auto-submitted
- [ ] Item transitions to completed on success
- [ ] Item transitions to failed on error
- [ ] Status persists during queue pause

### Queue Behavior
- [ ] Multiple items show correct individual statuses
- [ ] Only active item has pulse animation
- [ ] Auto-submit triggers correct status changes
- [ ] Edit/delete preserves status indicators
- [ ] Queue pause doesn't affect status display

### Edge Cases
- [ ] Failed item doesn't trigger auto-submit
- [ ] Completing cleared item doesn't error
- [ ] Multiple processing state changes handled smoothly
- [ ] Status changes reflect in UI within 300ms

## Related Features

- **Queue Pause/Resume**: Queue pause button (⏸/▶) allows pausing auto-submit
- **Queue Panel**: Desktop (300px sidebar) and mobile (bottom sheet) responsive UI
- **Auto-submit**: Automatic submission of next queued item on completion
- **Request Queue**: FIFO queue for multiple concurrent requests

## Files Modified

- `webui/dist/app.js` - Queue state and functions
- `webui/dist/app.css` - Status indicator styling and animations
- `webui/dist/index.html` - Queue panel HTML (unchanged, uses CSS classes)

## Performance Considerations

- Status updates are O(n) where n = queue length (linear search)
- Re-render only when status changes (renderQueuePanel called once)
- CSS animations handled by GPU (transform/opacity)
- No polling or continuous updates

## Future Enhancements

- [ ] Expandable queue items showing detailed error messages
- [ ] Status history (track all status changes)
- [ ] Retry button for failed items
- [ ] Bulk operations (select multiple for delete/retry)
- [ ] Queue statistics (avg processing time, success rate)
- [ ] Sound notification on completion/failure

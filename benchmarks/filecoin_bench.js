
import fs from 'fs';
import { pushToFilecoin } from '../utils/filecoin.js';

const NUM_RUNS = 10;
const OUTPUT_FILE = 'metrics/filecoin_results.json';

function average(arr) {
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

function median(arr) {
  const sorted = [...arr].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[mid - 1] + sorted[mid]) / 2
    : sorted[mid];
}

function percentile(arr, p) {
  const sorted = [...arr].sort((a, b) => a - b);
  const index = Math.floor(sorted.length * p);
  return sorted[index];
}

async function runFilecoinBenchmark() {
  console.log('Filecoin Storage Benchmark');
  console.log('='.repeat(60));
  console.log(`Runs: ${NUM_RUNS}\n`);

  const uploadLatencies = [];
  const fileSizes = [];
  let successCount = 0;
  let failCount = 0;
  const results = [];

  for (let i = 0; i < NUM_RUNS; i++) {
    console.log(`Run ${i + 1}/${NUM_RUNS}:`);

    const testData = {
      run: i + 1,
      timestamp: new Date().toISOString(),
      cccd: `test_${Date.now()}_${i}`,
      data: `Sample medical record ${i}`.repeat(10)
    };

    const filename = `benchmark_${i + 1}.json`;

    try {
      const startTime = Date.now();

      const uploadResult = await pushToFilecoin(
        testData,
        filename,
        `Benchmark test ${i + 1}`
      );

      const endTime = Date.now();
      const latency = endTime - startTime;

      uploadLatencies.push(latency);
      const size = Buffer.from(JSON.stringify(testData)).length;
      fileSizes.push(size);
      successCount++;

      console.log(`  Upload: ${latency}ms`);
      console.log(`  Size: ${size} bytes`);
      console.log(`  Provider: ${uploadResult.provider}`);
      console.log(`  CID: ${uploadResult.cid}\n`);

      results.push({
        run: i + 1,
        success: true,
        latency_ms: latency,
        size_bytes: size,
        provider: uploadResult.provider,
        cid: uploadResult.cid
      });

    } catch (error) {
      failCount++;
      console.log(`  FAILED: ${error.message}\n`);

      results.push({
        run: i + 1,
        success: false,
        error: error.message
      });
    }

    await new Promise(resolve => setTimeout(resolve, 500));
  }

  const stats = {
    config: {
      num_runs: NUM_RUNS,
      timestamp: new Date().toISOString()
    },
    success_rate: (successCount / NUM_RUNS) * 100,
    num_success: successCount,
    num_failed: failCount,
    raw_results: results
  };

  if (uploadLatencies.length > 0) {
    stats.upload_latency = {
      mean: average(uploadLatencies),
      median: median(uploadLatencies),
      min: Math.min(...uploadLatencies),
      max: Math.max(...uploadLatencies),
      p50: percentile(uploadLatencies, 0.50),
      p95: percentile(uploadLatencies, 0.95),
      p99: uploadLatencies.length > 10 ? percentile(uploadLatencies, 0.99) : Math.max(...uploadLatencies)
    };

    stats.file_size = {
      mean: average(fileSizes),
      median: median(fileSizes),
      min: Math.min(...fileSizes),
      max: Math.max(...fileSizes)
    };
  }

  fs.mkdirSync('metrics', { recursive: true });
  fs.writeFileSync(OUTPUT_FILE, JSON.stringify(stats, null, 2));

  console.log('='.repeat(60));
  console.log('Filecoin Benchmark Results');
  console.log('='.repeat(60));
  console.log(`\nSuccess Rate: ${stats.success_rate.toFixed(1)}% (${successCount}/${NUM_RUNS})`);

  if (uploadLatencies.length > 0) {
    console.log('\nUpload Latency (ms):');
    console.log(`  Mean:   ${stats.upload_latency.mean.toFixed(2)}`);
    console.log(`  Median: ${stats.upload_latency.median.toFixed(2)}`);
    console.log(`  p50:    ${stats.upload_latency.p50.toFixed(2)}`);
    console.log(`  p95:    ${stats.upload_latency.p95.toFixed(2)}`);
    console.log(`  Range:  ${stats.upload_latency.min} - ${stats.upload_latency.max}`);

    console.log('\nFile Size (bytes):');
    console.log(`  Mean:   ${stats.file_size.mean.toFixed(0)}`);
    console.log(`  Median: ${stats.file_size.median.toFixed(0)}`);
  }

  if (failCount > 0) {
    console.log(`\nFailures: ${failCount}`);
  }

  console.log(`\nResults saved to: ${OUTPUT_FILE}`);
}

runFilecoinBenchmark().catch(console.error);

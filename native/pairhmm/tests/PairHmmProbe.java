// SPDX-License-Identifier: MIT
import com.intel.gkl.pairhmm.IntelPairHmm;
import com.intel.gkl.pairhmm.IntelPairHmmOMP;
import java.io.PrintWriter;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Random;
import org.broadinstitute.gatk.nativebindings.pairhmm.HaplotypeDataHolder;
import org.broadinstitute.gatk.nativebindings.pairhmm.PairHMMNativeArguments;
import org.broadinstitute.gatk.nativebindings.pairhmm.ReadDataHolder;

/** Run in separate JVMs with stock and CUDA classpaths; compare output bits.
 * Covers #1131 boundaries, ambiguous bases, varying GCP, double rescue and
 * the long-read CPU fallback, using the public GATK native binding interface.
 */
public final class PairHmmProbe {
    private static byte[] bases(Random rng, int length) {
        byte[] result = new byte[length];
        byte[] alphabet = {'A', 'C', 'G', 'T', 'N'};
        for (int i = 0; i < length; ++i) result[i] = alphabet[rng.nextInt(alphabet.length)];
        return result;
    }

    private static byte[] qualities(Random rng, int length, int minimum, int span) {
        byte[] result = new byte[length];
        for (int i = 0; i < length; ++i) result[i] = (byte)(minimum + rng.nextInt(span));
        return result;
    }

    public static void main(String[] args) throws Exception {
        int batches = Integer.parseInt(args[1]);
        int threads = Integer.parseInt(args[2]);
        int[] edges = {1, 7, 8, 9, 31, 32, 33, 127, 128, 129, 150, 151, 257, 1024, 1025};
        try (PrintWriter output = new PrintWriter(Path.of(args[0]).toFile())) {
            for (boolean doubles : new boolean[] {false, true}) {
                IntelPairHmm hmm = threads == 1 ? new IntelPairHmm() : new IntelPairHmmOMP();
                if (!hmm.load(null)) throw new IllegalStateException("Native PairHMM did not load");
                PairHMMNativeArguments options = new PairHMMNativeArguments();
                options.maxNumberOfThreads = threads;
                options.useDoublePrecision = doubles;
                hmm.initialize(options);
                Random rng = new Random(1131);
                for (int batch = 0; batch < batches; ++batch) {
                    ReadDataHolder[] reads = new ReadDataHolder[8];
                    HaplotypeDataHolder[] haps = new HaplotypeDataHolder[4];
                    int length = batch < edges.length ? edges[batch] : 1 + rng.nextInt(300);
                    byte[] template = bases(rng, length + 40);
                    for (int h = 0; h < haps.length; ++h) {
                        haps[h] = new HaplotypeDataHolder();
                        haps[h].haplotypeBases = h == 0 ? template :
                                bases(rng, 1 + rng.nextInt(length + 80));
                    }
                    for (int r = 0; r < reads.length; ++r) {
                        ReadDataHolder read = new ReadDataHolder();
                        read.readBases = r == 0 ? Arrays.copyOf(template, length) : bases(rng, length);
                        read.readQuals = qualities(rng, length, 2, 60);
                        read.insertionGOP = qualities(rng, length, 2, 60);
                        read.deletionGOP = qualities(rng, length, 2, 60);
                        read.overallGCP = qualities(rng, length, 2, 30);
                        reads[r] = read;
                    }
                    double[] values = new double[reads.length * haps.length];
                    hmm.computeLikelihoods(reads, haps, values);
                    double[] repeat = new double[values.length];
                    hmm.computeLikelihoods(reads, haps, repeat);
                    if (!Arrays.equals(values, repeat)) throw new IllegalStateException("Non-repeatable batch");
                    for (int i = 0; i < values.length; ++i) {
                        if (Double.isNaN(values[i]) || values[i] == Double.POSITIVE_INFINITY)
                            throw new IllegalStateException("Invalid likelihood");
                        output.printf("%s\t%d\t%d\t%s%n", doubles, batch, i,
                                Long.toHexString(Double.doubleToRawLongBits(values[i])));
                    }
                }
                hmm.done();
            }
        }
    }
}

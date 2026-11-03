// This file is part of the SV-Benchmarks collection of verification tasks:
// https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks
//
// SPDX-FileCopyrightText: 2025 Hassan Mousavi <University of Tehran>
// SPDX-License-Identifier: Apache-2.0
/*
* This benchmark task is an original contribution developed for the
* "float-UnboundedLoop" benchmark set, proposed for SV-COMP 2026.

* License:
*   Licensed under the Apache License, Version 2.0 (the "License");
*   you may not use this file except in compliance with the License.
*   You may obtain a copy of the License at:
*
*       http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*/
import org.sosy_lab.sv_benchmarks.Verifier;

public class Main {
  public static void main(String[] args) {
    double x = 0.0;
    int p = 0;
    while (Verifier.nondetBoolean()) {
      if (p == 0) {
        for (int i = 0; i < 2; i++) x = x + 1.0;
        p = 1;
      } else if (p == 1) {
        for (int i = 0; i < 2; i++) x = x - 1.0;
        p = 2;
      } else {
        p = 0;
      }
      if (x > 20.0) x = 0.0;
      if (x < 0.0) x = 0.0;
    }
    assert (x >= 0.0 && x <= 20.0 && p >= 0 && p <= 2);
  }
}
